"""Strict, bounded policy language and the public action contract."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

FactName = Literal["confidential", "external", "approved"]
Effect = Literal["ALLOW", "BLOCK", "HUMAN_REVIEW"]
Nonempty = Annotated[str, Field(min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Atom(StrictModel):
    op: Literal["atom"]
    name: FactName


class Not(StrictModel):
    op: Literal["not"]
    child: Condition


class All(StrictModel):
    op: Literal["all"]
    children: Annotated[tuple[Condition, ...], Field(min_length=1)]


class AnyOf(StrictModel):
    op: Literal["any"]
    children: Annotated[tuple[Condition, ...], Field(min_length=1)]


Condition = Annotated[Union[Atom, Not, All, AnyOf], Field(discriminator="op")]
for _node in (Not, All, AnyOf):
    _node.model_rebuild()


class PolicyRule(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
    source_text: Nonempty
    effect: Effect
    condition: Condition

    @field_validator("condition", mode="before")
    @classmethod
    def bound_ast(cls, value):
        # Count before recursive Pydantic validation, including malformed inputs.
        pending, count = [(value, 1)], 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > 50 or depth > 8:
                raise ValueError("condition exceeds 50 nodes or depth 8")
            if isinstance(node, BaseModel):
                node = node.model_dump(mode="python")
            if isinstance(node, dict):
                if "child" in node:
                    pending.append((node["child"], depth + 1))
                children = node.get("children", ())
                if isinstance(children, (list, tuple)):
                    if len(children) > 50:
                        raise ValueError("condition exceeds 50 nodes")
                    pending.extend((c, depth + 1) for c in children)
        return value


class PolicyBundle(StrictModel):
    revision: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
    rules: Annotated[tuple[PolicyRule, ...], Field(max_length=20)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule IDs")
        return self


class ActionContext(StrictModel):
    confidential: StrictBool
    external: StrictBool
    approved: StrictBool


class Decision(StrictModel):
    decision: Effect
    matched_policy_ids: tuple[str, ...] = ()
    reason: str


class DocumentSummary(StrictModel):
    id: str
    name: str
    classification: str | None


class ActionResult(Decision):
    request_id: str
    executed: bool
    policy_revision: str | None
    outbox_id: str | None = None


class DraftItem(StrictModel):
    source_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    source_text: Nonempty
    status: Literal["supported", "unsupported", "refused"]
    rule: PolicyRule | None = None
    explanation: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "supported":
            if self.rule is None or self.rule.source_text != self.source_text:
                raise ValueError("supported item requires a rule with identical source text")
        elif self.rule is not None or not self.explanation:
            raise ValueError("unresolved item requires an explanation and no rule")
        return self


class PolicyDraft(StrictModel):
    items: Annotated[tuple[DraftItem, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.source_id for item in self.items}) != len(self.items):
            raise ValueError("duplicate source IDs")
        rules = [item.rule for item in self.items if item.rule]
        if len({rule.id for rule in rules}) != len(rules):
            raise ValueError("duplicate rule IDs")
        return self

    @property
    def accepted(self) -> bool:
        return all(item.status == "supported" for item in self.items)
