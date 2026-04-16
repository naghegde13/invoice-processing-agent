"""
models.py - LangGraph state definition.
In LangGraph 1.1.6, list fields use Annotated with operator.add
so the framework knows how to merge them when nodes return partial state.
"""
from typing import TypedDict, Optional, List, Annotated
import operator


class InvoiceState(TypedDict):
    invoice_path: str
    raw_text:     str
    extracted:    Optional[dict]
    validation:   Optional[dict]
    approval:     Optional[dict]
    payment:      Optional[dict]
    fraud:        Optional[dict]
    status:       str
    # Annotated[List[str], operator.add]: LangGraph accumulates list items across agent returns
    # Each agent appends to errors[] and log[]; framework merges via operator.add (concatenation)
    # Without this, last agent's list would overwrite prior agents' entries
    errors:       Annotated[List[str], operator.add]
    log:          Annotated[List[str], operator.add]
