"""AsOfLab: a local-first temporal replay and evaluation workbench.

The question it answers: what could this prediction pipeline actually know at
time T, and how much do data-timing and revision mistakes change its
evaluation?
"""

from asoflab.contract import CONTRACT_VERSION

__version__ = "0.1.0"

__all__ = ["CONTRACT_VERSION", "__version__"]
