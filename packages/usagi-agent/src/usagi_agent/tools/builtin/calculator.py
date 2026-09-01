"""Small arithmetic evaluator with no Python evaluation or name access."""
from __future__ import annotations

import ast
import operator
from decimal import Decimal

from usagi_agent.ports import ToolContext
from usagi_agent.tools.adapter import ToolAdapter
from usagi_agent.types.tool import ToolSpec

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent is too large")
        return Decimal(str(_BINARY[type(node.op)](left, right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    raise ValueError("unsupported arithmetic expression")


class CalculatorTool(ToolAdapter):
    spec = ToolSpec(
        name="calculator",
        description="Evaluate a basic arithmetic expression.",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        expression = str(arguments["expression"])
        if len(expression) > 256:
            raise ValueError("expression is too long")
        result = _evaluate(ast.parse(expression, mode="eval"))
        return {"expression": expression, "result": str(result)}
