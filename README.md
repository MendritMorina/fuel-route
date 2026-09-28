# Fuel Route API

A Django 6.1 + Django REST Framework API that returns a US driving route, suggested fuel stops, and estimated fuel spend using the supplied OPIS fuel price CSV. The route is returned as GeoJSON, ready to draw with Leaflet, MapLibre, or another map client.

## Run locally

Requirements: Python 3.12+ and internet access for the free OSRM routing demo. Commands below work in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py runserver
```

In Git Bash, activate the environment with `source .venv/Scripts/activate` instead of the PowerShell activation command. Django's development server reloads when Python files change.

No database, migrations, API key, or data preparation step is required. The prebuilt `api/data.json` is included. The assessment CSV is kept out of Git; to rebuild the index, put `fuel-prices-for-be-assessment.csv` in the project root, download the [2025 US Census National Places Gazetteer ZIP](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip) there as `census-places-2025.zip`, then run `python scripts/build_data.py`.

## Test with Postman

Create a **POST** request to `http://127.0.0.1:8000/api/route/`, set `Content-Type: application/json`, and use this body:

```json
{"start":"Los Angeles, CA","finish":"Chicago, IL"}
```

Other examples: `Chicago, IL` → `Saint Louis, MO`, and `Dallas, TX` → `Houston, TX`. The first request needs an OSRM network call; a repeated request uses a one-hour in-memory cache. `GET /health/` returns `{"status":"ok"}`.

You can also open `http://127.0.0.1:8000/api/route/` in a browser to see DRF's browsable API. The endpoint itself accepts POST, so a browser GET shows method 405 and the interactive POST form. The serializer checks that `start` and `finish` are strings; the service checks whether those cities exist in the US place index.

Selected response fields:

```json
{
  "distance_miles": 2029.5,
  "route": {"type": "LineString", "coordinates": [[-118.24, 34.05], [-118.23, 34.06]]},
  "fuel_stops": [
    {"name": "...", "city": "...", "price_per_gallon_usd": 3.0,
     "gallons": 20.0, "cost_usd": 60.0, "mile_marker": 500.0,
     "route_point": [-115.0, 36.0]}
  ],
  "initial_fuel": {"gallons": 50.0, "cost_usd": 150.0, "price_basis": "..."},
  "total_fuel_gallons": 202.95,
  "total_fuel_cost_usd": 646.11
}
```

The numbers above illustrate the format; live route and price data determine actual results. `route.coordinates` are `[longitude, latitude]`. Each stop has a route point for drawing a marker and a separate approximate station city center.

## How it works

The DRF structure follows the [beginner guide shared for this project](https://medium.com/@michal.drozdze/setting-up-a-django-api-with-django-rest-framework-drf-a-beginners-guide-cee5d61f00a6): `rest_framework` is installed, `RouteRequestSerializer` checks the request fields, `RoutePlanView` handles POST, `core/urls.py` includes `api/urls.py`, and the browsable API is enabled. The guide's database model is unnecessary here because the supplied prices are static CSV data.

1. Locations are resolved from an offline index of US Census place names. Input format is `City, ST` with a two-letter state abbreviation, avoiding geocoding calls.
2. One OSRM Route API call gets the full driving geometry, road distance, and duration. Identical routes are cached for one hour.
3. Stations from the CSV are indexed to Census city centers at build time. A spatial grid finds cities within 25 miles of the route. Duplicate stations at the same approximate location are reduced to the cheapest price.
4. A fuel purchase algorithm chooses the first cheaper reachable stop; if there is none, it buys enough to reach the cheapest reachable stop. The tank holds 50 gallons (500 miles at 10 mpg). The destination acts as a zero-price terminal node, so no unused fuel is bought for the end of the trip.

## Assumptions and limits

- The trip starts with an empty tank. Fuel bought at the origin is priced using the nearest listed station and included in `total_fuel_cost_usd`. This makes short trips have a meaningful cost.
- CSV rows have no coordinates. Stops use **city centers**, so the station address and highway access must be verified before driving. The suggested stop is a price-based estimate, not a turn-by-turn detour route. Detour mileage and cost are not included.
- The optimization minimizes listed fuel expense along the returned route. It does not charge for time spent stopping, so it may suggest several small purchases if that reduces fuel cost.
- The public OSRM demo is suitable for this assessment and has usage limits and no uptime guarantee. Production use should point `OSRM_URL` to a hosted or self-hosted OSRM instance. Route data © OpenStreetMap contributors. City coordinates: US Census 2025 Gazetteer.
- Set `ROUTING_USER_AGENT` to an identifying value for your deployment; the default identifies this assessment app.
- Local memory caching is per process. Use a shared cache such as Redis when running multiple workers.

## Check the project

```powershell
python manage.py check
```

Use the Postman request above to verify the route and fuel cost end to end.

## Five-minute demo outline

Show the POST request in Postman, identify the GeoJSON route and ordered `fuel_stops`, then show `total_fuel_cost_usd`. Repeat the request to demonstrate the cache. In code, show `core/urls.py` → `api/urls.py` → `api/views.py` → `api/serializers.py` → `api/services.py`, and explain the offline data generation in `scripts/build_data.py`. A Loom video and GitHub link must be created and shared by the applicant; neither is generated by this repository.

GitHub repository: [MendritMorina/fuel-route](https://github.com/MendritMorina/fuel-route). Record the Loom separately and include both links in your submission.
