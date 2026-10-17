"""Requirements belong to the work, even after it is assigned to a person."""


def requires_cashier(task: dict) -> bool:
    def cashier(value):
        return str(value or "").strip().upper() == "CASHIER"

    if cashier(task.get("required_role")) or cashier(task.get("assigned_to")):
        return True
    # Older reassignments replaced the role with a user id. Their recorded
    # provenance still identifies the work; no data migration is required.
    return any(
        isinstance(entry, dict)
        and entry.get("action") in {"reassigned", "escalated"}
        and cashier(entry.get("from"))
        for entry in task.get("history") or []
    )
