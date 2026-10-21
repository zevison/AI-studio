"""本地付费预算上限与异常消费熔断。

设计约束：
  * 只统计本机 usage_events，实际扣费优先、缺失时用估算补齐；
  * 金额上限与「窗口内付费提交次数」熔断互相独立，0 表示不启用该项；
  * 付费提交必须在消费确认令牌之前被拦截，避免产生任何付费调用。
"""
import ast
import time
from pathlib import Path

import pytest

from core import cost_system, db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def budget(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'studio.db')
    db.init_db()
    cfg = {
        'paid_budget_usd': 5.0,
        'paid_budget_window_hours': 24,
        'paid_budget_max_tasks': 20,
    }
    monkeypatch.setattr(cost_system.settings, 'get_all', lambda: dict(cfg))
    return cfg


def _usage(actual=None, estimated=None, age_seconds=60):
    db.execute(
        'INSERT INTO usage_events(id,task_id,kind,model,units,estimated_cost_usd,actual_cost_usd,metadata_json,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?)',
        (db.new_id('ev'), 'task', 'video', 'doubao-seedance-2-5-260628', 10, estimated, actual, '{}',
         time.time() - age_seconds),
    )


def test_below_limits_is_not_blocked(budget):
    _usage(actual=1.5)
    _usage(estimated=1.5)
    status = cost_system.paid_budget_status(0.5)
    assert status['blocked'] is False
    assert status['spent_usd'] == 3.0
    assert status['actual_usd'] == 1.5
    assert status['estimated_usd'] == 1.5
    assert status['tasks'] == 2
    assert status['projected_usd'] == 3.5


def test_actual_cost_wins_over_estimate(budget):
    # 同一笔任务只要已有实际扣费，就不应该再叠加估算。
    db.execute(
        'INSERT INTO usage_events(id,task_id,kind,model,units,estimated_cost_usd,actual_cost_usd,metadata_json,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?)',
        (db.new_id('ev'), 'task', 'video', 'm', 10, 9.0, 2.0, '{}', time.time()),
    )
    assert cost_system.paid_budget_status()['spent_usd'] == 2.0


def test_amount_cap_blocks_the_next_paid_submission(budget):
    _usage(actual=4.8)
    status = cost_system.paid_budget_status(0.5)
    assert status['blocked'] is True
    assert '预算上限' in status['reasons'][0]


def test_task_count_breaker_is_independent_of_amount(budget):
    budget['paid_budget_usd'] = 0.0
    budget['paid_budget_max_tasks'] = 3
    for _ in range(3):
        _usage(actual=None, estimated=None)
    status = cost_system.paid_budget_status()
    assert status['blocked'] is True
    assert status['unresolved_tasks'] == 3
    assert any('熔断' in r for r in status['reasons'])


def test_events_outside_the_window_are_ignored(budget):
    _usage(actual=99.0, age_seconds=48 * 3600)
    status = cost_system.paid_budget_status()
    assert status['spent_usd'] == 0.0
    assert status['blocked'] is False


def test_disabled_switches_never_block(budget):
    budget['paid_budget_usd'] = 0.0
    budget['paid_budget_max_tasks'] = 0
    _usage(actual=1000.0)
    status = cost_system.paid_budget_status(100.0)
    assert status['enabled'] is False
    assert status['blocked'] is False


def test_gate_runs_before_the_paid_claim():
    """源码级回归：预算检查必须在 claim_video_submission 之前，避免令牌被消耗。"""
    source = (ROOT / 'app.py').read_text('utf8')
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_direct_video_generate')
    calls = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in ('paid_budget_status', 'claim_video_submission', 'video_create'):
                calls.append((node.lineno, name))
    order = [name for _, name in sorted(calls)]
    assert order.index('paid_budget_status') < order.index('claim_video_submission') < order.index('video_create')
