"""Phase 5 tests: DAG Validation & Cycle Detection.

Tests:
- Valid DAG creation (diamond, linear, branched)
- Empty graph rejection (422)
- Duplicate node key rejection (422)
- Missing/unknown edge node reference rejection (422)
- Self-loop rejection (422)
- Cycle rejection via Kahn's algorithm (422)
- Duplicate edge rejection (422)
- Unsupported task type rejection (422)
- Structural payload validation per handler type (422)
"""

import os
import pytest
from app.services.workflow_engine import validate_workflow
from app.services.errors import APIError

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL and Redis",
)


def _make_node(key: str, task_type: str = "sleep", payload: dict | None = None) -> dict:
    if payload is None:
        payload = {"seconds": 0.01} if task_type == "sleep" else {}
        if task_type == "csv_stats":
            payload = {"csv_data": "a,b\n1,2"}
        elif task_type == "http_check":
            payload = {"url": "http://example.com"}
        elif task_type == "image_resize":
            payload = {"image_b64": "abc", "width": 10, "height": 10}
    return {"node_key": key, "task_type": task_type, "payload": payload}


def test_valid_diamond_dag_accepted():
    """Diamond DAG: A -> B, C -> D should pass validation without errors."""
    nodes = [
        _make_node("A"),
        _make_node("B"),
        _make_node("C"),
        _make_node("D"),
    ]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "A", "to": "C"},
        {"from": "B", "to": "D"},
        {"from": "C", "to": "D"},
    ]
    validate_workflow(nodes, edges)


def test_valid_linear_dag_accepted():
    """Linear DAG: A -> B -> C should pass validation."""
    nodes = [_make_node("A"), _make_node("B"), _make_node("C")]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "B", "to": "C"},
    ]
    validate_workflow(nodes, edges)


def test_empty_nodes_rejected():
    """Workflow with no nodes should raise 422."""
    with pytest.raises(APIError) as exc:
        validate_workflow([], [])
    assert exc.value.status_code == 422
    assert "at least one node" in str(exc.value.details).lower()


def test_duplicate_node_key_rejected():
    """Two nodes with the same key must be rejected with 422."""
    nodes = [
        _make_node("step-1"),
        _make_node("step-1"),
    ]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, [])
    assert exc.value.status_code == 422
    assert "duplicate node key" in str(exc.value.details).lower()


def test_missing_edge_node_rejected():
    """Edge referencing a non-existent node key must be rejected with 422."""
    nodes = [_make_node("A"), _make_node("B")]
    edges = [{"from": "A", "to": "Z"}]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, edges)
    assert exc.value.status_code == 422
    assert "unknown target node" in str(exc.value.details).lower()


def test_self_loop_rejected():
    """Self-loop edge (A -> A) must be rejected with 422."""
    nodes = [_make_node("A")]
    edges = [{"from": "A", "to": "A"}]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, edges)
    assert exc.value.status_code == 422
    assert "self-loop" in str(exc.value.details).lower()


def test_simple_cycle_rejected():
    """Direct cycle (A -> B -> A) must be rejected with 422."""
    nodes = [_make_node("A"), _make_node("B")]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "B", "to": "A"},
    ]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, edges)
    assert exc.value.status_code == 422
    assert "cycle detected" in str(exc.value.details).lower()


def test_complex_cycle_rejected():
    """Multi-node cycle (A -> B -> C -> D -> B) must be rejected with 422."""
    nodes = [_make_node("A"), _make_node("B"), _make_node("C"), _make_node("D")]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "B", "to": "C"},
        {"from": "C", "to": "D"},
        {"from": "D", "to": "B"},
    ]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, edges)
    assert exc.value.status_code == 422
    assert "cycle detected" in str(exc.value.details).lower()


def test_duplicate_edge_rejected():
    """Duplicate identical edges must be rejected with 422."""
    nodes = [_make_node("A"), _make_node("B")]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "A", "to": "B"},
    ]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, edges)
    assert exc.value.status_code == 422
    assert "duplicate edge" in str(exc.value.details).lower()


def test_unsupported_task_type_rejected():
    """Node with unknown task type must be rejected with 422."""
    nodes = [{"node_key": "A", "task_type": "invalid_type_xyz", "payload": {}}]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, [])
    assert exc.value.status_code == 422
    assert "unsupported task type" in str(exc.value.details).lower()


def test_missing_payload_required_fields_rejected():
    """Node missing required payload fields must be rejected with 422."""
    # sleep missing seconds
    nodes = [{"node_key": "A", "task_type": "sleep", "payload": {}}]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes, [])
    assert exc.value.status_code == 422
    assert "requires 'seconds'" in str(exc.value.details)

    # http_check missing url
    nodes2 = [{"node_key": "B", "task_type": "http_check", "payload": {}}]
    with pytest.raises(APIError) as exc:
        validate_workflow(nodes2, [])
    assert exc.value.status_code == 422
    assert "requires 'url'" in str(exc.value.details)
