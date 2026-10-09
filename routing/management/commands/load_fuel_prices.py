import csv
import io
import requests
from django.core.management.base import BaseCommand
from routing.models import TruckStop

US_CITIES_URL = 'https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv'


class Command(BaseCommand):
    help = 'Load fuel prices from CSV and geocode truck stop locations'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the fuel prices CSV file')

    def build_city_lookup(self):
        self.stdout.write("Downloading US cities coordinates database...")
        resp = requests.get(US_CITIES_URL, timeout=30)
        resp.raise_for_status()
        lookup = {}
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            city = row['CITY'].strip().upper()
            state = row['STATE_CODE'].strip().upper()
            key = (city, state)
            if key not in lookup:
                lookup[key] = (float(row['LATITUDE']), float(row['LONGITUDE']))
        self.stdout.write(f"Loaded {len(lookup)} city coordinates")
        return lookup

    def handle(self, *args, **options):
        csv_file = options['csv_file']

        city_lookup = self.build_city_lookup()

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

        matched = 0
        unmatched = 0
        total = len(stops)

        for i, (opis_id, data) in enumerate(stops.items(), 1):
            key = (data['city'].upper(), data['state'].upper())
            coords = city_lookup.get(key)
            lat = coords[0] if coords else None
            lon = coords[1] if coords else None

            if coords:
                matched += 1
            else:
                unmatched += 1

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

        self.stdout.write(f"Geocoded: {matched}, Unmatched: {unmatched}")
        self.stdout.write(self.style.SUCCESS(f"Successfully loaded {total} truck stops"))
