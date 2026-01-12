from typing import Any, Dict, List


def process_records(records: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    filters = config["filters"]
    company_field = filters["company_field"]
    company_value = filters["company_value"]

    directions_cfg = config["directions"]
    direction_field = directions_cfg["field"]
    mapping = directions_cfg["mapping"]
    default_direction = directions_cfg.get("default", "OTHER")

    dedup_keys = config["dedup_keys"]

    filtered = [r for r in records if r.get(company_field) == company_value]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[tuple] = set()

    for record in filtered:
        dedup_key = tuple(record.get(key) for key in dedup_keys)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        raw_direction = record.get(direction_field)
        direction = mapping.get(raw_direction, default_direction)
        grouped.setdefault(direction, []).append(record)

    return grouped
