import csv
import time
import requests
from django.core.management.base import BaseCommand
from routing.models import TruckStop


class Command(BaseCommand):
    help = 'Load fuel prices from CSV and geocode truck stop locations'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the fuel prices CSV file')
        parser.add_argument('--skip-geocode', action='store_true', help='Skip geocoding (load CSV data only)')

    def geocode(self, address, city, state):
        query = f"{address}, {city}, {state}, USA"
        url = 'https://nominatim.openstreetmap.org/search'
        params = {
            'q': query,
            'format': 'json',
            'limit': 1,
            'countrycodes': 'us',
        }
        headers = {'User-Agent': 'FuelRouteOptimizer/1.0'}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            results = resp.json()
            if results:
                return float(results[0]['lat']), float(results[0]['lon'])
        except (requests.RequestException, ValueError, KeyError, IndexError):
            pass

        # Fallback: try city + state only
        params['q'] = f"{city}, {state}, USA"
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            results = resp.json()
            if results:
                return float(results[0]['lat']), float(results[0]['lon'])
        except (requests.RequestException, ValueError, KeyError, IndexError):
            pass

        return None, None

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        skip_geocode = options['skip_geocode']

        # Read CSV and group by opis_id, keeping cheapest price
        stops = {}
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                opis_id = int(row['OPIS Truckstop ID'])
                price = float(row['Retail Price'])
                if opis_id not in stops or price < stops[opis_id]['price']:
                    stops[opis_id] = {
                        'opis_id': opis_id,
                        'name': row['Truckstop Name'].strip(),
                        'address': row['Address'].strip(),
                        'city': row['City'].strip(),
                        'state': row['State'].strip(),
                        'rack_id': int(row['Rack ID']),
                        'price': price,
                    }

        self.stdout.write(f"Found {len(stops)} unique truck stops (kept cheapest price per stop)")

        TruckStop.objects.all().delete()

        total = len(stops)
        for i, (opis_id, data) in enumerate(stops.items(), 1):
            lat, lon = None, None
            if not skip_geocode:
                lat, lon = self.geocode(data['address'], data['city'], data['state'])
                # Nominatim rate limit: 1 request per second
                time.sleep(1.1)

            TruckStop.objects.create(
                opis_id=data['opis_id'],
                name=data['name'],
                address=data['address'],
                city=data['city'],
                state=data['state'],
                rack_id=data['rack_id'],
                retail_price=data['price'],
                latitude=lat,
                longitude=lon,
            )

            if i % 50 == 0 or i == total:
                self.stdout.write(f"Processed {i}/{total} stops")

        self.stdout.write(self.style.SUCCESS(f"Successfully loaded {total} truck stops"))
