from collections.abc import Callable
from typing import Any

from tools.casualty import casualty_estimate
from tools.hospital import hospital_capacity, receiving_capacity, transport_options
from tools.inventory import stock_levels, vehicle_inventory
from tools.population import zone_population
from tools.roads import road_status
from tools.shelter import shelter_capacity

TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "hospital_capacity": hospital_capacity,
    "receiving_capacity": receiving_capacity,
    "transport_options": transport_options,
    "casualty_estimate": casualty_estimate,
    "shelter_capacity": shelter_capacity,
    "zone_population": zone_population,
    "road_status": road_status,
    "vehicle_inventory": vehicle_inventory,
    "stock_levels": stock_levels,
}
