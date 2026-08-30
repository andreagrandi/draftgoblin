from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from draftomen.public_dump import (
    PublicDumpChecksumError,
    PublicDumpError,
    PUBLIC_DUMP_MAX_ISSUE_DETAILS,
    PublicDumpManifest,
    PublicDumpParseError,
    PublicDumpReader,
    PublicDumpSource,
    iter_public_dump_rows,
    read_public_dump,
    write_public_dump_manifest,
)
from draftomen.seventeen import iter_17lands_draft_data_rows


CSV_TEXT = "draft_id,pick\ndraft-one,Card One\ndraft-two,Card Two\n"


def test_manifest_bytes_are_canonical_and_preserve_provenance(tmp_path: Path) -> None:
    local = PublicDumpSource(
        name="local",
        path="local.csv",
        sha256="A" * 64,
        retrieved_at="2026-08-30T12:00:00Z",
        attribution="Example publisher",
        license="CC BY 4.0",
    )
    remote = PublicDumpSource(
        name="remote",
        url="https://example.test/dump.csv.gz",
        sha256="B" * 64,
        retrieved_at="2026-08-29T12:00:00Z",
        attribution="Remote publisher",
        license="ODbL",
    )
    manifest = PublicDumpManifest(sources=(remote, local))

    assert manifest.sources == (local, remote)
    assert manifest.to_bytes() == manifest.to_bytes()
    assert manifest.to_bytes() == (
        b'{"schema_version":1,"sources":[{"attribution":"Example publisher",'
        b'"license":"CC BY 4.0","name":"local","path":"local.csv",'
        b'"retrieved_at":"2026-08-30T12:00:00Z","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
        b'{"attribution":"Remote publisher","license":"ODbL","name":"remote",'
        b'"retrieved_at":"2026-08-29T12:00:00Z","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"url":"https://example.test/dump.csv.gz"}]}\n'
    )
    assert PublicDumpManifest.from_bytes(manifest.to_bytes()) == manifest

    manifest_path = tmp_path / "manifest.json"
    assert write_public_dump_manifest(manifest, manifest_path) == str(manifest_path)
    assert PublicDumpManifest.from_bytes(manifest_path.read_bytes()) == manifest


def test_checksum_is_verified_before_rows_are_exposed(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    reader = PublicDumpReader(PublicDumpSource(name="rows", path=path, sha256=digest))
    assert list(reader.iter_rows()) == [
        {"draft_id": "draft-one", "pick": "Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]
    assert reader.report is not None
    assert reader.report.rows_yielded == 2

    mismatch = PublicDumpReader(
        PublicDumpSource(name="rows", path=path, sha256="0" * 64)
    )
    with pytest.raises(PublicDumpChecksumError):
        mismatch.iter_rows()
    assert mismatch.report is None


def test_pinned_iterator_uses_verified_snapshot_after_source_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = PublicDumpReader(PublicDumpSource(name="rows", path=path, sha256=digest))

    rows = reader.iter_rows()
    path.write_text("draft_id,pick\nreplacement,Wrong Row\n", encoding="utf-8")

    assert list(rows) == [
        {"draft_id": "draft-one", "pick": "Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]


def test_manifest_requires_a_nonempty_set_of_pinned_sources() -> None:
    with pytest.raises(PublicDumpError):
        PublicDumpManifest(sources=())

    source = PublicDumpSource(name="unpinned", url="https://example.test/dump.csv")
    with pytest.raises(PublicDumpError):
        PublicDumpManifest(sources=(source,))


@pytest.mark.parametrize("field_name", ["attribution", "license"])
def test_provenance_fields_reject_null_values(field_name: str) -> None:
    with pytest.raises(PublicDumpError):
        PublicDumpSource(
            name="source",
            path="rows.csv",
            **{field_name: None},
        )

    descriptor = {
        "name": "source",
        "path": "rows.csv",
        "sha256": "a" * 64,
        field_name: None,
    }
    with pytest.raises(PublicDumpError):
        PublicDumpSource.from_json(descriptor)


def test_blank_top_level_records_are_reported_with_physical_row_numbers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.csv"
    path.write_text(
        "draft_id,pick\n"
        "\n"
        "draft-one,Card One\n"
        "\n"
        "draft-two,Card Two\n",
        encoding="utf-8",
    )

    reader = PublicDumpReader(PublicDumpSource(name="rows", path=path))
    assert list(reader.iter_rows()) == [
        {"draft_id": "draft-one", "pick": "Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]
    report = reader.report
    assert report is not None
    assert report.rows_seen == 4
    assert report.rows_yielded == 2
    assert report.rows_skipped == 2
    assert report.skip_reasons == {"blank_row": 2}
    assert [issue.row_number for issue in report.issues] == [2, 4]


def test_blank_physical_lines_inside_quoted_fields_are_not_blank_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.csv"
    path.write_text(
        "draft_id,pick\n"
        'draft-one,"Card One\n'
        '\n'
        'still Card One"\n'
        "draft-two,Card Two\n",
        encoding="utf-8",
    )

    reader = PublicDumpReader(PublicDumpSource(name="rows", path=path))
    assert list(reader.iter_rows()) == [
        {"draft_id": "draft-one", "pick": "Card One\n\nstill Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]
    report = reader.report
    assert report is not None
    assert report.skip_reasons == {}
    assert report.issues == ()


def test_plain_gzip_and_tar_csv_inputs_share_reader_behavior(tmp_path: Path) -> None:
    plain = tmp_path / "rows.csv"
    plain.write_text(CSV_TEXT, encoding="utf-8")

    compressed = tmp_path / "rows.csv.gz"
    with gzip.open(compressed, mode="wt", encoding="utf-8", newline="") as output:
        output.write(CSV_TEXT)

    archived = tmp_path / "rows.tar.gz"
    payload = CSV_TEXT.encode("utf-8")
    with tarfile.open(archived, mode="w:gz") as archive:
        member = tarfile.TarInfo("rows.csv")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    expected = [
        {"draft_id": "draft-one", "pick": "Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]
    assert list(iter_public_dump_rows(plain)) == expected
    assert list(iter_public_dump_rows(compressed)) == expected
    assert read_public_dump(archived).rows == tuple(expected)


def test_malformed_row_report_is_structured_and_privacy_safe(tmp_path: Path) -> None:
    path = tmp_path / "private.csv"
    path.write_text(
        "draft_id,pick\n"
        "private-draft-001,Private Card\n"
        "private-draft-002,Private Card Two,identifier-value\n"
        "private-draft-003\n",
        encoding="utf-8",
    )

    reader = PublicDumpReader(PublicDumpSource(name="private", path=path))
    assert list(reader.iter_rows()) == [{"draft_id": "private-draft-001", "pick": "Private Card"}]
    report = reader.report
    assert report is not None
    assert report.source_name == "private"
    assert report.rows_seen == 3
    assert report.rows_yielded == 1
    assert report.rows_skipped == 2
    assert report.skip_reasons == {"extra_fields": 1, "missing_fields": 1}
    assert report.error_reasons == {}
    assert [issue.row_number for issue in report.issues] == [3, 4]

    serialized = json.dumps(report.to_json(), sort_keys=True)
    assert "private-draft-001" not in serialized
    assert "Private Card" not in serialized
    assert "identifier-value" not in serialized
    assert "draft_id" not in serialized
    assert "pick" not in serialized


def test_malformed_csv_reports_error_without_row_values(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text(
        "draft_id,pick\n"
        "private-draft-001,Private Card\n"
        "private-draft-002,\"unterminated\n",
        encoding="utf-8",
    )
    reader = PublicDumpReader(PublicDumpSource(name="broken", path=path))

    with pytest.raises(PublicDumpParseError) as raised:
        list(reader.iter_rows())

    report = raised.value.report
    assert report.error_reasons == {"malformed_csv": 1}
    assert report.rows_seen == 1
    serialized = json.dumps(report.to_json(), sort_keys=True)
    assert "private-draft" not in serialized
    assert "Private Card" not in serialized


def test_issue_details_are_bounded_without_losing_aggregate_counts(
    tmp_path: Path,
) -> None:
    blank_row_count = PUBLIC_DUMP_MAX_ISSUE_DETAILS * 100 + 1
    private_identifier = "private-draft-001"
    private_value = "Private Card"
    path = tmp_path / "malformed-heavy.csv"
    path.write_text(
        "draft_id,pick\n"
        + "\n" * blank_row_count
        + f'{private_identifier},"{private_value}\n',
        encoding="utf-8",
    )

    reader = PublicDumpReader(PublicDumpSource(name="private", path=path))
    with pytest.raises(PublicDumpParseError) as raised:
        list(reader.iter_rows())

    report = raised.value.report
    assert report.rows_seen == blank_row_count
    assert report.rows_yielded == 0
    assert report.rows_skipped == blank_row_count
    assert report.skip_reasons == {"blank_row": blank_row_count}
    assert report.error_reasons == {"malformed_csv": 1}
    assert len(report.issues) == PUBLIC_DUMP_MAX_ISSUE_DETAILS
    assert [issue.row_number for issue in report.issues] == list(
        range(2, PUBLIC_DUMP_MAX_ISSUE_DETAILS + 2)
    )
    assert report.omitted_issue_details == (
        blank_row_count + 1 - PUBLIC_DUMP_MAX_ISSUE_DETAILS
    )

    expected_json = {
        "source_name": "private",
        "rows_seen": blank_row_count,
        "rows_yielded": 0,
        "rows_skipped": blank_row_count,
        "skip_reasons": {"blank_row": blank_row_count},
        "error_reasons": {"malformed_csv": 1},
        "issues": [
            {"reason": "blank_row", "row_number": row_number, "source_name": "private"}
            for row_number in range(2, PUBLIC_DUMP_MAX_ISSUE_DETAILS + 2)
        ],
        "omitted_issue_details": blank_row_count + 1 - PUBLIC_DUMP_MAX_ISSUE_DETAILS,
    }
    report_json = report.to_json()
    assert report_json == expected_json
    serialized = json.dumps(report_json, sort_keys=True)
    assert serialized == json.dumps(expected_json, sort_keys=True)
    assert private_identifier not in serialized
    assert private_value not in serialized
    assert "draft_id" not in serialized
    assert "pick" not in serialized


def test_seventeen_compatibility_preserves_valid_row_mappings(tmp_path: Path) -> None:
    path = tmp_path / "draft-data.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")

    assert list(iter_17lands_draft_data_rows(path=path)) == [
        {"draft_id": "draft-one", "pick": "Card One"},
        {"draft_id": "draft-two", "pick": "Card Two"},
    ]
