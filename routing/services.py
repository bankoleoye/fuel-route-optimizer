import logging
import math
import requests
import polyline as polyline_codec
from routing.models import TruckStop

logger = logging.getLogger(__name__)

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
OSRM_URL = 'http://router.project-osrm.org/route/v1/driving'
HEADERS = {'User-Agent': 'FuelRouteOptimizer/1.0'}

VEHICLE_RANGE_MILES = 500
MPG = 10
# Search corridor: how far off the route (in miles) to look for stops
CORRIDOR_MILES = 20


def geocode(location):
    logger.info("Geocoding location: %s", location)
    params = {
        'q': f"{location}, USA",
        'format': 'json',
        'limit': 1,
        'countrycodes': 'us',
    }
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Geocoding request failed for '%s': %s", location, e)
        raise
    results = resp.json()
    if not results:
        logger.warning("No geocoding results for: %s", location)
        raise ValueError(f"Could not geocode location: {location}")
    lat, lon = float(results[0]['lat']), float(results[0]['lon'])
    logger.info("Geocoded '%s' -> (%s, %s)", location, lat, lon)
    return lat, lon


def get_route(start_coords, finish_coords):
    start_str = f"{start_coords[1]},{start_coords[0]}"
    finish_str = f"{finish_coords[1]},{finish_coords[0]}"
    url = f"{OSRM_URL}/{start_str};{finish_str}"
    params = {
        'overview': 'full',
        'geometries': 'polyline',
        'steps': 'false',
    }
    logger.info("Requesting route from OSRM: %s -> %s", start_str, finish_str)
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("OSRM request failed: %s", e)
        raise
    data = resp.json()
    if data.get('code') != 'Ok':
        logger.error("OSRM routing error: %s", data.get('message', 'Unknown error'))
        raise ValueError(f"OSRM routing failed: {data.get('message', 'Unknown error')}")
    route = data['routes'][0]
    geometry = route['geometry']
    distance_meters = route['distance']
    distance_miles = distance_meters * 0.000621371
    decoded_points = polyline_codec.decode(geometry)
    logger.info("Route received: %.2f miles, %d geometry points", distance_miles, len(decoded_points))
    return {
        'geometry': geometry,
        'decoded_points': decoded_points,
        'total_distance_miles': round(distance_miles, 2),
    }


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def compute_mile_markers(decoded_points):
    markers = [0.0]
    for i in range(1, len(decoded_points)):
        prev = decoded_points[i - 1]
        curr = decoded_points[i]
        dist = haversine(prev[0], prev[1], curr[0], curr[1])
        markers.append(markers[-1] + dist)
    return markers


def find_stops_along_route(decoded_points, mile_markers):
    stops = TruckStop.objects.filter(latitude__isnull=False, longitude__isnull=False)

    # Bounding box filter to reduce candidates
    lats = [p[0] for p in decoded_points]
    lons = [p[1] for p in decoded_points]
    buffer = CORRIDOR_MILES / 69.0  # rough degrees
    stops = stops.filter(
        latitude__gte=min(lats) - buffer,
        latitude__lte=max(lats) + buffer,
        longitude__gte=min(lons) - buffer,
        longitude__lte=max(lons) + buffer,
    )
    logger.info("Bounding box filter: %d candidate stops from DB", stops.count())

    # For each stop, find the closest point on the route and its mile marker
    route_stops = []
    # Sample route points to speed up distance checks
    sample_step = max(1, len(decoded_points) // 500)
    sampled = [(i, decoded_points[i]) for i in range(0, len(decoded_points), sample_step)]

    for stop in stops:
        min_dist = float('inf')
        closest_idx = 0
        for idx, point in sampled:
            dist = haversine(stop.latitude, stop.longitude, point[0], point[1])
            if dist < min_dist:
                min_dist = dist
                closest_idx = idx
        if min_dist <= CORRIDOR_MILES:
            route_stops.append({
                'stop': stop,
                'distance_from_route_miles': round(min_dist, 2),
                'mile_marker': round(mile_markers[closest_idx], 2),
            })

    route_stops.sort(key=lambda x: x['mile_marker'])
    logger.info("Found %d truck stops within %d-mile corridor", len(route_stops), CORRIDOR_MILES)
    return route_stops


def optimize_fuel_stops(route_stops, total_distance):
    if total_distance <= VEHICLE_RANGE_MILES:
        logger.info("Route is %.2f miles — within vehicle range, no stops needed", total_distance)
        total_gallons = total_distance / MPG
        if route_stops:
            cheapest = min(route_stops, key=lambda s: s['stop'].retail_price)
            return [], round(total_gallons * cheapest['stop'].retail_price, 2)
        return [], 0.0

    fuel_stops = []
    current_mile = 0.0
    remaining_range = VEHICLE_RANGE_MILES

    while current_mile + remaining_range < total_distance:
        max_reach = current_mile + remaining_range
        # Only consider stops in the second half of our range to avoid unnecessary early stops
        min_mile = current_mile + (remaining_range * 0.5)
        reachable = [
            s for s in route_stops
            if min_mile <= s['mile_marker'] <= max_reach
        ]

        # If nothing in the second half, expand to full range
        if not reachable:
            reachable = [
                s for s in route_stops
                if current_mile < s['mile_marker'] <= max_reach
            ]

        if not reachable:
            # No stops in range — pick the nearest one ahead
            ahead = [s for s in route_stops if s['mile_marker'] > current_mile]
            if not ahead:
                logger.warning("No more stops ahead at mile %.2f — ending optimization", current_mile)
                break
            reachable = [ahead[0]]
            logger.warning("No stops in range at mile %.2f — using nearest ahead: %s at mile %.2f",
                           current_mile, ahead[0]['stop'].name, ahead[0]['mile_marker'])

        # Pick the cheapest reachable stop
        best = min(reachable, key=lambda s: s['stop'].retail_price)

        distance_traveled = best['mile_marker'] - current_mile
        gallons = distance_traveled / MPG
        cost = round(gallons * best['stop'].retail_price, 2)

        logger.info("Fuel stop selected: %s (%s, %s) at mile %.2f — $%.3f/gal, %.2f gallons, $%.2f",
                     best['stop'].name, best['stop'].city, best['stop'].state,
                     best['mile_marker'], best['stop'].retail_price, gallons, cost)

        fuel_stops.append({
            'name': best['stop'].name,
            'address': best['stop'].address,
            'city': best['stop'].city,
            'state': best['stop'].state,
            'latitude': best['stop'].latitude,
            'longitude': best['stop'].longitude,
            'price_per_gallon': best['stop'].retail_price,
            'distance_from_start_miles': best['mile_marker'],
            'gallons_filled': round(gallons, 2),
            'fuel_cost_at_stop': cost,
        })

        current_mile = best['mile_marker']
        remaining_range = VEHICLE_RANGE_MILES

    # Calculate cost for final leg
    final_distance = total_distance - current_mile
    final_gallons = final_distance / MPG
    if fuel_stops:
        final_cost = round(final_gallons * fuel_stops[-1]['price_per_gallon'], 2)
    else:
        final_cost = 0.0

    total_fuel_cost = sum(s['fuel_cost_at_stop'] for s in fuel_stops) + final_cost
    logger.info("Optimization complete: %d stops, total fuel cost $%.2f", len(fuel_stops), total_fuel_cost)

    return fuel_stops, round(total_fuel_cost, 2)
