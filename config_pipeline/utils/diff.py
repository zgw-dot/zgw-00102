import json


def compute_diff(current_config, target_config):
    """Compute the difference between two configurations.
    
    Returns a dict with 'added', 'removed', 'modified' keys.
    """
    diff = {
        "added": {},
        "removed": {},
        "modified": {},
    }

    if current_config is None:
        diff["added"] = target_config
        return diff

    current_keys = set(current_config.keys())
    target_keys = set(target_config.keys())

    for key in target_keys - current_keys:
        diff["added"][key] = target_config[key]

    for key in current_keys - target_keys:
        diff["removed"][key] = current_config[key]

    for key in current_keys & target_keys:
        current_val = current_config[key]
        target_val = target_config[key]
        if current_val != target_val:
            if isinstance(current_val, dict) and isinstance(target_val, dict):
                nested_diff = compute_diff(current_val, target_val)
                if nested_diff["added"] or nested_diff["removed"] or nested_diff["modified"]:
                    diff["modified"][key] = nested_diff
            elif isinstance(current_val, list) and isinstance(target_val, list):
                if current_val != target_val:
                    diff["modified"][key] = {
                        "old": current_val,
                        "new": target_val,
                    }
            else:
                diff["modified"][key] = {
                    "old": current_val,
                    "new": target_val,
                }

    return diff


def has_changes(diff):
    """Check if the diff contains any changes."""
    return bool(diff["added"] or diff["removed"] or diff["modified"])


def format_diff(diff, indent=0):
    """Format the diff for human-readable output."""
    lines = []
    prefix = "  " * indent

    for key, value in sorted(diff["added"].items()):
        if isinstance(value, dict):
            lines.append(f"{prefix}+ {key}:")
            lines.extend(_format_dict(value, indent + 1, "+"))
        elif isinstance(value, list):
            lines.append(f"{prefix}+ {key}: {json.dumps(value)}")
        else:
            lines.append(f"{prefix}+ {key}: {json.dumps(value)}")

    for key, value in sorted(diff["removed"].items()):
        if isinstance(value, dict):
            lines.append(f"{prefix}- {key}:")
            lines.extend(_format_dict(value, indent + 1, "-"))
        elif isinstance(value, list):
            lines.append(f"{prefix}- {key}: {json.dumps(value)}")
        else:
            lines.append(f"{prefix}- {key}: {json.dumps(value)}")

    for key, value in sorted(diff["modified"].items()):
        if "old" in value and "new" in value:
            lines.append(f"{prefix}~ {key}:")
            if isinstance(value["old"], list) or isinstance(value["new"], list):
                lines.append(f"{prefix}  - old: {json.dumps(value['old'])}")
                lines.append(f"{prefix}  + new: {json.dumps(value['new'])}")
            else:
                lines.append(f"{prefix}  - old: {json.dumps(value['old'])}")
                lines.append(f"{prefix}  + new: {json.dumps(value['new'])}")
        else:
            lines.append(f"{prefix}~ {key}:")
            lines.extend(format_diff(value, indent + 1))

    return lines


def _format_dict(d, indent, symbol):
    lines = []
    prefix = "  " * indent
    for key, value in sorted(d.items()):
        if isinstance(value, dict):
            lines.append(f"{prefix}{symbol} {key}:")
            lines.extend(_format_dict(value, indent + 1, symbol))
        else:
            lines.append(f"{prefix}{symbol} {key}: {json.dumps(value)}")
    return lines


def generate_plan_summary(diff):
    """Generate a summary of the plan for audit purposes."""
    summary = {
        "total_changes": 0,
        "added_count": len(_count_changes(diff["added"])),
        "removed_count": len(_count_changes(diff["removed"])),
        "modified_count": len(_count_changes(diff["modified"])),
        "added_keys": list(_count_changes(diff["added"])),
        "removed_keys": list(_count_changes(diff["removed"])),
        "modified_keys": list(_count_changes(diff["modified"])),
    }
    summary["total_changes"] = (
        summary["added_count"] + summary["removed_count"] + summary["modified_count"]
    )
    return summary


def _count_changes(obj, prefix=""):
    """Count the number of leaf-level changes in a diff object."""
    changes = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                if "old" in value and "new" in value:
                    changes.add(full_key)
                else:
                    changes.update(_count_changes(value, full_key))
            else:
                changes.add(full_key)
    return changes
