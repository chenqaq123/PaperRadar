#!/usr/bin/env python3
"""Conference registry for the reusable paper-analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConferenceSpec:
    key: str
    display_name: str
    site_type: str
    base_url: str
    pmlr_volumes: dict[int, int] = field(default_factory=dict)
    anthology_volumes: dict[int, str] = field(default_factory=dict)


CONFERENCE_REGISTRY: dict[str, ConferenceSpec] = {
    "cvpr": ConferenceSpec(
        key="cvpr",
        display_name="CVPR",
        site_type="cvf_virtual",
        base_url="https://cvpr.thecvf.com",
    ),
    "iccv": ConferenceSpec(
        key="iccv",
        display_name="ICCV",
        site_type="cvf_virtual",
        base_url="https://iccv.thecvf.com",
    ),
    "wacv": ConferenceSpec(
        key="wacv",
        display_name="WACV",
        site_type="cvf_virtual",
        base_url="https://wacv.thecvf.com",
    ),
    "eccv": ConferenceSpec(
        key="eccv",
        display_name="ECCV",
        site_type="ecva",
        base_url="https://www.ecva.net",
    ),
    "icml": ConferenceSpec(
        key="icml",
        display_name="ICML",
        site_type="pmlr_bib",
        base_url="https://proceedings.mlr.press",
        pmlr_volumes={
            2021: 139,
            2022: 162,
            2023: 202,
            2024: 235,
            2025: 267,
        },
    ),
    "iclr": ConferenceSpec(
        key="iclr",
        display_name="ICLR",
        site_type="openreview_iclr",
        base_url="https://openreview.net",
    ),
    "neurips": ConferenceSpec(
        key="neurips",
        display_name="NeurIPS",
        site_type="neurips_proceedings",
        base_url="https://papers.nips.cc",
    ),
    "acl": ConferenceSpec(
        key="acl",
        display_name="ACL",
        site_type="acl_anthology",
        base_url="https://aclanthology.org",
        anthology_volumes={
            2021: "2021.acl-long",
            2022: "2022.acl-long",
            2023: "2023.acl-long",
            2024: "2024.acl-long",
            2025: "2025.acl-long",
        },
    ),
    "emnlp": ConferenceSpec(
        key="emnlp",
        display_name="EMNLP",
        site_type="acl_anthology",
        base_url="https://aclanthology.org",
        anthology_volumes={
            2021: "2021.emnlp-main",
            2022: "2022.emnlp-main",
            2023: "2023.emnlp-main",
            2024: "2024.emnlp-main",
            2025: "2025.emnlp-main",
        },
    ),
}


def resolve_conference(key: str) -> ConferenceSpec:
    normalized = key.strip().lower()
    if normalized not in CONFERENCE_REGISTRY:
        supported = ", ".join(sorted(CONFERENCE_REGISTRY))
        raise KeyError(f"Unsupported conference '{key}'. Supported conferences: {supported}")
    return CONFERENCE_REGISTRY[normalized]


def make_run_slug(conference_key: str, year: int) -> str:
    return f"{conference_key.lower()}{year}"
