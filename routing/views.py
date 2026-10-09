import logging
import time
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from routing.serializers import RouteRequestSerializer
from routing.services import geocode, get_route, compute_mile_markers, find_stops_along_route, optimize_fuel_stops

logger = logging.getLogger(__name__)


class RouteView(APIView):
    def post(self, request):
        request_start = time.time()
        logger.info("Route request received: %s", request.data)

        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Invalid request data: %s", serializer.errors)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start = serializer.validated_data['start']
        finish = serializer.validated_data['finish']

        try:
            start_coords = geocode(start)
            finish_coords = geocode(finish)
        except ValueError as e:
            logger.error("Geocoding failed: %s", e)
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Geocoding service error: %s", e)
            return Response({'error': 'Geocoding service unavailable'}, status=status.HTTP_502_BAD_GATEWAY)

        try:
            route_data = get_route(start_coords, finish_coords)
        except ValueError as e:
            logger.error("Routing failed: %s", e)
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error("Routing service error: %s", e)
            return Response({'error': 'Routing service unavailable'}, status=status.HTTP_502_BAD_GATEWAY)

        mile_markers = compute_mile_markers(route_data['decoded_points'])
        route_stops = find_stops_along_route(route_data['decoded_points'], mile_markers)
        fuel_stops, total_fuel_cost = optimize_fuel_stops(route_stops, route_data['total_distance_miles'])

        elapsed = round(time.time() - request_start, 2)
        logger.info("Route request completed in %.2fs: %s -> %s (%.2f miles, %d stops, $%.2f)",
                     elapsed, start, finish, route_data['total_distance_miles'],
                     len(fuel_stops), total_fuel_cost)

        return Response({
            'route': {
                'start': start,
                'finish': finish,
                'start_coordinates': {'latitude': start_coords[0], 'longitude': start_coords[1]},
                'finish_coordinates': {'latitude': finish_coords[0], 'longitude': finish_coords[1]},
                # 'geometry': route_data['geometry'],
                'total_distance_miles': route_data['total_distance_miles'],
            },
            'fuel_stops': fuel_stops,
            'total_fuel_cost': total_fuel_cost,
            'vehicle_assumptions': {
                'max_range_miles': 500,
                'mpg': 10,
            },
        })
