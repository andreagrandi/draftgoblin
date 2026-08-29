from __future__ import annotations

import json
from pathlib import Path

import pytest

import draftomen.set_profile as set_profile_module

from draftomen.config import COLOR_PAIRS
from draftomen.semantic_roles import CompiledRoleProfile, ProfileCard, Role, RoleAssignment
from draftomen.set_profile import (
    PairProfile,
    ProfileMaturity,
    SetProfile,
    SetProfileError,
    SetProfileSchemaError,
    dump_set_profile,
    load_set_profile,
    safe_load_set_profile,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "set-profiles"


def test_mature_profile_round_trip_covers_all_pairs_and_optional_sections() -> None:
    profile = load_set_profile(
        FIXTURE_DIR / "mature.json",
        expected_set_code="TST",
        expected_format="QuickDraft",
    )

    assert profile.maturity is ProfileMaturity.MATURE
    assert tuple(item.pair for item in profile.pairs) == COLOR_PAIRS
    assert sum(item.theme is not None for item in profile.pairs) == 2
    samples = profile.samples
    assert samples is not None
    assert samples.count_for("RG") == 120
    wu = profile.pair("WU")
    assert wu is not None
    assert wu.theme == "tempo flyers"
    wb = profile.pair("WB")
    assert wb is not None
    assert wb.theme is None
    rg = profile.pair("RG")
    assert rg is not None
    assert rg.theme == "landfall pressure"
    assert wu.structural_targets[0].name == "average_land_count"
    assert wu.role_targets[0].role is Role.DRAW
    assert wu.removal_targets[0].kind == "disable"
    assert wu.synergy[0].first_card == "oracle_id:wu-bomb"
    assert wu.scarcity[0].card_key == "oracle_id:wu-bomb"
    assert SetProfile.from_json(profile.to_json()).to_bytes() == profile.to_bytes()

def test_profile_fingerprint_is_stable_across_round_trip() -> None:
    profile = load_set_profile(FIXTURE_DIR / "mature.json")
    equivalent = SetProfile.from_json(profile.to_json())

    assert profile.fingerprint
    assert equivalent.fingerprint == profile.fingerprint


def test_pair_profile_theme_round_trip_trims_and_omits_absent_theme() -> None:
    themed = PairProfile.from_json({"pair": " wu ", "theme": "  tempo flyers  "})

    assert themed.theme == "tempo flyers"
    assert themed.to_json() == {"pair": "WU", "theme": "tempo flyers"}
    assert PairProfile.from_json({"pair": "WB"}).theme is None
    assert "theme" not in PairProfile(pair="WB").to_json()


@pytest.mark.parametrize("theme", ("", "   ", 42, False))
def test_pair_profile_theme_rejects_blank_and_non_string_values(theme: object) -> None:
    with pytest.raises(SetProfileSchemaError, match="pair_profile.theme"):
        PairProfile.from_json({"pair": "WU", "theme": theme})


def test_sparse_early_profile_preserves_only_available_empirical_evidence() -> None:
    profile = load_set_profile(FIXTURE_DIR / "early.json")

    assert profile.maturity is ProfileMaturity.EARLY
    samples = profile.samples
    assert samples is not None
    assert samples.by_pair == (("WU", 17),)
    assert samples.count_for("WB") is None
    assert tuple(item.pair for item in profile.pair_profiles) == ("WU",)
    assert profile.pair("WU") is not None
    assert profile.pair("WB") is None
    serialized = profile.to_json()
    assert serialized["samples"] == {"by_pair": {"WU": 17}, "total": 17}
    assert serialized["pair_profiles"] == [
        {
            "pair": "WU",
            "structural_targets": [{"name": "average_land_count", "value": 17.0}],
        }
    ]


def test_metadata_and_semantic_only_profiles_omit_empirical_sections() -> None:
    metadata = load_set_profile(FIXTURE_DIR / "metadata-only.json")
    semantic = load_set_profile(FIXTURE_DIR / "semantic-only.json")

    assert metadata.maturity is ProfileMaturity.METADATA_ONLY
    assert metadata.samples is None
    assert semantic.maturity is ProfileMaturity.SEMANTIC_ONLY
    assert semantic.samples is None
    assert semantic.pair_profiles == (PairProfile(pair="WU", theme="tempo flyers"),)
    semantic_json = semantic.to_json()
    assert "samples" not in semantic_json
    assert semantic_json["pair_profiles"] == [{"pair": "WU", "theme": "tempo flyers"}]


def test_unknown_optional_fields_are_ignored_and_output_is_stable(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    payload["unknown_optional"] = {"future": [1, 2, 3]}
    payload["pair_profiles"][0]["unknown_optional"] = "ignored"
    profile = SetProfile.from_json(payload)

    output = dump_set_profile(profile, tmp_path / "profile.json")
    assert output.read_bytes() == profile.to_bytes()
    assert "unknown_optional" not in output.read_text(encoding="utf-8")


def test_domain_graph_is_deeply_immutable() -> None:
    profile = load_set_profile(FIXTURE_DIR / "mature.json")
    serialized = profile.to_json()
    serialized["set_code"] = "other"
    assert profile.set_code == "tst"
    assert isinstance(serialized, dict)
    with pytest.raises(AttributeError):
        profile.pairs[0].structural_targets = ()  # type: ignore[misc]
    assert isinstance(profile.pairs, tuple)
    assert isinstance(profile.pairs[0].structural_targets, tuple)
    role_profile = profile.role_profile
    assert role_profile is not None
    assert isinstance(role_profile.cards, tuple)


def test_strict_parser_rejects_missing_required_duplicate_unknown_and_future_schema() -> None:
    payload = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    payload.pop("confidence")
    with pytest.raises(SetProfileSchemaError, match="confidence"):
        SetProfile.from_json(payload)

    duplicate = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    duplicate["pair_profiles"].append({"pair": "wu"})
    with pytest.raises(SetProfileSchemaError, match="duplicate"):
        SetProfile.from_json(duplicate)

    unknown_pair = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    unknown_pair["pair_profiles"] = [{"pair": "XX"}]
    with pytest.raises(SetProfileSchemaError, match="Unsupported color pair"):
        SetProfile.from_json(unknown_pair)

    with pytest.raises(SetProfileSchemaError, match="Unsupported set profile schema"):
        load_set_profile(FIXTURE_DIR / "future-schema.json")


def test_strict_loader_rejects_future_nested_role_schema_and_safe_loader_ignores_roles(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "semantic-only.json").read_text(encoding="utf-8"))
    payload["role_profile"]["profile_schema_version"] = 999
    path = tmp_path / "future-role-schema.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SetProfileSchemaError, match="profile_schema_version"):
        load_set_profile(path)

    result = safe_load_set_profile("tst", "quickdraft", profile_path=path)
    assert result.source == "generic"
    assert result.profile.role_profile is None
    resolution = result.profile.resolve_roles(
        {
            "oracle_id": "wu-bomb",
            "name": "WU Bomb",
            "set": "tst",
            "oracle_text": "Draw a card.",
        }
    )
    assert resolution.source == "local_classifier"


def test_maturity_invariants_reject_inconsistent_evidence_labels() -> None:
    mature_without_evidence = json.loads((FIXTURE_DIR / "mature.json").read_text(encoding="utf-8"))
    mature_without_evidence.pop("samples")
    mature_without_evidence.pop("pair_profiles")
    with pytest.raises(SetProfileSchemaError, match="empirical evidence"):
        SetProfile.from_json(mature_without_evidence)

    early_without_evidence = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    early_without_evidence.pop("samples")
    early_without_evidence.pop("pair_profiles")
    with pytest.raises(SetProfileSchemaError, match="empirical evidence"):
        SetProfile.from_json(early_without_evidence)

    metadata_with_samples = json.loads((FIXTURE_DIR / "metadata-only.json").read_text(encoding="utf-8"))
    metadata_with_samples["samples"] = {"total": 1}
    with pytest.raises(SetProfileSchemaError, match="cannot contain empirical evidence"):
        SetProfile.from_json(metadata_with_samples)
    metadata_with_semantic = json.loads((FIXTURE_DIR / "metadata-only.json").read_text(encoding="utf-8"))
    semantic_payload = json.loads((FIXTURE_DIR / "semantic-only.json").read_text(encoding="utf-8"))
    metadata_with_semantic["role_profile"] = semantic_payload["role_profile"]
    with pytest.raises(SetProfileSchemaError, match="semantic evidence"):
        SetProfile.from_json(metadata_with_semantic)

    semantic_without_roles = semantic_payload
    semantic_without_roles.pop("role_profile")
    with pytest.raises(SetProfileSchemaError, match="must contain semantic evidence"):
        SetProfile.from_json(semantic_without_roles)

    semantic_with_pair = json.loads((FIXTURE_DIR / "semantic-only.json").read_text(encoding="utf-8"))
    semantic_with_pair["pair_profiles"] = [
        {"pair": "WU", "structural_targets": [{"name": "lands", "value": 17}]}
    ]
    with pytest.raises(SetProfileSchemaError, match="cannot contain empirical evidence"):
        SetProfile.from_json(semantic_with_pair)


def test_safe_loader_precedence_prefers_mature_then_early_then_semantic_then_metadata() -> None:
    result = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_paths=(
            FIXTURE_DIR / "metadata-only.json",
            FIXTURE_DIR / "semantic-only.json",
            FIXTURE_DIR / "early.json",
            FIXTURE_DIR / "mature.json",
        ),
    )
    assert result.source == "local-mature"
    assert result.profile.maturity is ProfileMaturity.MATURE

    semantic_result = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_paths=(
            FIXTURE_DIR / "metadata-only.json",
            FIXTURE_DIR / "semantic-only.json",
        ),
    )
    assert semantic_result.source == "local-semantic-only"
    assert semantic_result.profile.maturity is ProfileMaturity.SEMANTIC_ONLY


def test_safe_loader_rejects_wrong_target_and_direct_missing_corrupt_future_fallbacks(tmp_path: Path) -> None:
    wrong_target = json.loads((FIXTURE_DIR / "early.json").read_text(encoding="utf-8"))
    wrong_target["set_code"] = "other"
    wrong_path = tmp_path / "wrong-target.json"
    wrong_path.write_text(json.dumps(wrong_target), encoding="utf-8")
    wrong_result = safe_load_set_profile("tst", "quickdraft", profile_path=wrong_path)
    assert wrong_result.source == "generic"
    assert wrong_result.profile.set_code == "tst"
    assert wrong_result.profile.event_format == "quickdraft"
    assert any("does not match requested set" in diagnostic for diagnostic in wrong_result.diagnostics)

    for fixture_name in ("missing.json", "corrupt.json", "future-schema.json"):
        result = safe_load_set_profile(
            "tst",
            "quickdraft",
            profile_path=FIXTURE_DIR / fixture_name,
        )
        assert result.source == "generic"
        assert result.profile.samples is None
        assert result.profile.pair_profiles == ()
        assert any("rejected:" in diagnostic for diagnostic in result.diagnostics)


@pytest.mark.parametrize("method", ("expanduser", "resolve"))
def test_safe_loader_candidate_discovery_failures_fall_back_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    last_valid = load_set_profile(FIXTURE_DIR / "semantic-only.json")

    def fail_candidate_discovery(*args: object, **kwargs: object) -> None:
        raise OSError("simulated candidate discovery failure")

    monkeypatch.setattr(Path, method, fail_candidate_discovery)

    generic = safe_load_set_profile("tst", "quickdraft", profile_path=tmp_path / "missing.json")
    assert generic.source == "generic"
    assert any(f":{method}:" in diagnostic for diagnostic in generic.diagnostics)

    from_last_valid = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_path=tmp_path / "missing.json",
        last_valid_profile=last_valid,
    )
    assert from_last_valid.source == "last-valid"
    assert from_last_valid.profile is last_valid
    assert any(f":{method}:" in diagnostic for diagnostic in from_last_valid.diagnostics)


def test_recursive_json_decoder_failure_is_wrapped_and_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    last_valid = load_set_profile(FIXTURE_DIR / "semantic-only.json")
    path = tmp_path / "recursive.json"
    path.write_text("{}", encoding="utf-8")

    def fail_json_loads(*args: object, **kwargs: object) -> None:
        raise RecursionError("simulated recursive JSON")

    monkeypatch.setattr(set_profile_module.json, "loads", fail_json_loads)

    with pytest.raises(SetProfileError, match="Could not load set profile"):
        load_set_profile(path)

    generic = safe_load_set_profile("tst", "quickdraft", profile_path=path)
    assert generic.source == "generic"
    assert any("simulated recursive JSON" in diagnostic for diagnostic in generic.diagnostics)

    from_last_valid = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_path=path,
        last_valid_profile=last_valid,
    )
    assert from_last_valid.source == "last-valid"
    assert from_last_valid.profile is last_valid


def test_safe_loader_uses_compatible_last_valid_then_generic() -> None:
    last_valid = load_set_profile(FIXTURE_DIR / "semantic-only.json")
    from_last_valid = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_path=FIXTURE_DIR / "missing.json",
        last_valid_profile=last_valid,
    )
    assert from_last_valid.source == "last-valid"
    assert from_last_valid.profile is last_valid

    wrong_last = SetProfile(
        set_code="other",
        event_format=last_valid.event_format,
        profile_version=last_valid.profile_version,
        generated_at=last_valid.generated_at,
        source=last_valid.source,
        maturity=ProfileMaturity.METADATA_ONLY,
        samples=last_valid.samples,
        confidence=last_valid.confidence,
        pairs=last_valid.pairs,
        role_profile=None,
    )
    generic = safe_load_set_profile(
        "tst",
        "quickdraft",
        profile_path=FIXTURE_DIR / "missing.json",
        last_valid_profile=wrong_last,
    )
    assert generic.source == "generic"
    assert any("rejected:last-valid" in diagnostic for diagnostic in generic.diagnostics)


def test_semantic_roles_survive_absent_empirical_sections_and_incompatible_data_does_not_merge() -> None:
    profile = load_set_profile(FIXTURE_DIR / "semantic-only.json")
    card = {
        "oracle_id": "wu-bomb",
        "name": "WU Bomb",
        "set": "tst",
        "oracle_text": "Draw a card.",
    }
    resolved = profile.resolve_roles(card)
    assert resolved.source == "compiled_profile"
    role_profile = profile.role_profile
    assert role_profile is not None
    assert resolved.assignments == role_profile.cards[0].assignments

    incompatible = CompiledRoleProfile(
        set_code="tst",
        cards=(ProfileCard(key="oracle_id:wu-bomb", assignments=(RoleAssignment(Role.RAMP),)),),
        role_schema_version=999,
    )
    profile_with_incompatible_roles = SetProfile(
        set_code=profile.set_code,
        event_format=profile.event_format,
        profile_version=profile.profile_version,
        generated_at=profile.generated_at,
        source=profile.source,
        maturity=profile.maturity,
        samples=profile.samples,
        confidence=profile.confidence,
        pairs=profile.pairs,
        role_profile=incompatible,
    )
    fallback = profile_with_incompatible_roles.resolve_roles(card)
    assert fallback.source == "local_classifier"
    assert fallback.diagnostics == ("profile_incompatible_versions:used_local_classifier",)
    assert Role.RAMP not in fallback.assignments
