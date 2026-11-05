from __future__ import annotations

TRO_PUBLICATION_TITLE = "IEEE Transactions on Robotics"


def build_tro_query(
    *,
    start_record: int,
    max_records: int,
    start_year: int | None = None,
) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "format": "json",
        "publication_title": TRO_PUBLICATION_TITLE,
        "sort_field": "publication_year",
        "sort_order": "asc",
        "start_record": start_record,
        "max_records": max_records,
    }

    if start_year is not None:
        params["start_year"] = int(start_year)

    return params
