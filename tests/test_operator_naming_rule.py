"""`packs._operator_naming_rule` under both operator-name states."""

from central_command.config import settings
from central_command.runtime import packs


def test_rule_uses_the_configured_name(monkeypatch):
    monkeypatch.setattr(settings, "operator_name", "Jane Doe")
    rule = packs._operator_naming_rule()
    assert "'Jane Doe'" in rule and "never 'the operator'" in rule


def test_rule_points_at_the_setting_when_no_name_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "operator_name", "the operator")
    rule = packs._operator_naming_rule()
    assert "CC_OPERATOR_NAME" in rule and "'the operator'" not in rule.split("never")[0]
