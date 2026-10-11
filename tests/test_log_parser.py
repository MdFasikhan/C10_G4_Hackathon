import json

from tests.conftest import SAMPLE_LOGS
from tools.log_parser import parse_line, parse_log_file, redact


def test_parse_standard_log_line():
    line = '2026-10-10T09:12:02.450Z service=payment-api level=ERROR error_code=DB_TIMEOUT message="timeout"'
    event = parse_line(line)
    assert event is not None
    assert event.service == "payment-api"
    assert event.level == "ERROR"
    assert event.error_code == "DB_TIMEOUT"
    assert event.message == "timeout"
    assert event.timestamp.year == 2026 and event.timestamp.tzinfo is not None


def test_parse_invalid_line():
    event = parse_line("random text without structure")
    assert event is not None
    assert event.level == "INFO"


def test_explicit_level_wins_over_words_in_message():
    event = parse_line('service=api level=INFO message="recovered from ERROR state"')
    assert event.level == "INFO"


def test_warn_is_normalized_and_trace_id_extracted():
    event = parse_line('2026-10-10T09:12:03Z service=payment-db level=WARN message="pool 98%" trace_id=trc-1')
    assert event.level == "WARNING"
    assert event.trace_id == "trc-1"


def test_identical_lines_get_distinct_ids():
    a = parse_line("same line ERROR", line_number=1)
    b = parse_line("same line ERROR", line_number=2)
    assert a.event_id != b.event_id


def test_secrets_are_redacted():
    line = 'level=ERROR message="login failed" password=hunter2 Authorization: Bearer abc.def.ghi url=postgres://u:pw@db/x'
    event = parse_line(line)
    assert "hunter2" not in event.raw_line and "abc.def.ghi" not in event.raw_line and ":pw@" not in event.raw_line
    assert redact("key sk-or-v1-0123456789abcdef0123") == "key [REDACTED]"


def test_sample_log_files_parse():
    events = parse_log_file(SAMPLE_LOGS / "payment_api_incident.log")
    assert len(events) == 9
    assert [e.level for e in events].count("ERROR") == 5
    assert events[5].level == "CRITICAL"


def test_jsonl_json_array_and_csv(tmp_path):
    record = {"timestamp": "2026-10-10T09:00:00Z", "level": "error", "service": "api", "message": "boom", "trace_id": "t1"}
    jsonl = tmp_path / "a.jsonl"
    jsonl.write_text(json.dumps(record) + "\n\n" + json.dumps({**record, "level": "fatal"}) + "\n")
    assert [e.level for e in parse_log_file(jsonl)] == ["ERROR", "CRITICAL"]

    array = tmp_path / "a.json"
    array.write_text(json.dumps([record, record]))
    assert len(parse_log_file(array)) == 2

    csv_file = tmp_path / "a.csv"
    csv_file.write_text("timestamp,level,service,message\n2026-10-10 09:00:00,WARN,db,slow query\n")
    event = parse_log_file(csv_file)[0]
    assert (event.level, event.service, event.message) == ("WARNING", "db", "slow query")


def test_max_events_cap(tmp_path):
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"level=INFO message=m{i}" for i in range(50)))
    assert len(parse_log_file(path, max_events=10)) == 10
