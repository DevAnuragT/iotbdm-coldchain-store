"""Static world: holders (factories, depots, trucks, airports, clinics) and routes.

Everything derives from a seed, so the same seed yields the same world (N6).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from faker import Faker

from coldchain.models import Holder, HolderType

# Rough bounding box of a large country; coordinates are synthetic.
LAT_RANGE = (8.0, 32.0)
LON_RANGE = (68.0, 92.0)

# Resting temperature offsets by holder type (°C). Trucks and airport tarmacs run
# warmer than refrigerated rooms; this is what makes excursions cluster on legs.
TYPE_TEMP_OFFSET = {
    HolderType.FACTORY: -0.5,
    HolderType.DEPOT: -0.3,
    HolderType.TRUCK: 0.6,
    HolderType.AIRPORT: 0.9,
    HolderType.CLINIC: 0.1,
}
TYPE_DIURNAL_AMP = {
    HolderType.FACTORY: 0.1,
    HolderType.DEPOT: 0.2,
    HolderType.TRUCK: 0.9,
    HolderType.AIRPORT: 0.8,
    HolderType.CLINIC: 0.3,
}
# Relative excursion propensity per leg type (weights, not probabilities).
TYPE_EXCURSION_WEIGHT = {
    HolderType.FACTORY: 0.2,
    HolderType.DEPOT: 0.6,
    HolderType.TRUCK: 3.0,
    HolderType.AIRPORT: 2.5,
    HolderType.CLINIC: 0.8,
}


@dataclass(slots=True, frozen=True)
class Site:
    holder: Holder
    lat: float
    lon: float


@dataclass(slots=True)
class World:
    seed: int
    regions: list[str]
    sites: dict[int, Site]
    by_type: dict[HolderType, list[int]]

    @property
    def holders(self) -> list[Holder]:
        return [s.holder for s in self.sites.values()]

    def site(self, holder_id: int) -> Site:
        return self.sites[holder_id]


def build_world(
    seed: int,
    n_factories: int = 4,
    n_airports: int = 6,
    n_depots: int = 20,
    n_trucks: int = 80,
    n_clinics: int = 150,
    n_regions: int = 8,
) -> World:
    rng = np.random.default_rng([seed, 0x57])
    fake = Faker()
    Faker.seed(seed)

    regions = []
    while len(regions) < n_regions:
        r = fake.city()
        if r not in regions:
            regions.append(r)
    centers = {
        r: (rng.uniform(*LAT_RANGE), rng.uniform(*LON_RANGE)) for r in regions
    }

    sites: dict[int, Site] = {}
    by_type: dict[HolderType, list[int]] = {t: [] for t in HolderType}
    next_id = 1

    def add(htype: HolderType, name: str) -> None:
        nonlocal next_id
        region = regions[int(rng.integers(len(regions)))]
        clat, clon = centers[region]
        lat = float(np.clip(clat + rng.normal(0, 1.2), *LAT_RANGE))
        lon = float(np.clip(clon + rng.normal(0, 1.2), *LON_RANGE))
        h = Holder(next_id, name, htype, region)
        sites[next_id] = Site(h, lat, lon)
        by_type[htype].append(next_id)
        next_id += 1

    for i in range(n_factories):
        add(HolderType.FACTORY, f"{fake.company()} Plant {i + 1}")
    for i in range(n_airports):
        add(HolderType.AIRPORT, f"{fake.city()} Airport")
    for i in range(n_depots):
        add(HolderType.DEPOT, f"{fake.city()} Regional Depot")
    for i in range(n_trucks):
        add(HolderType.TRUCK, f"Truck {fake.bothify('??-##-????').upper()}")
    for i in range(n_clinics):
        add(HolderType.CLINIC, f"{fake.city()} Health Centre")

    return World(seed=seed, regions=regions, sites=sites, by_type=by_type)
