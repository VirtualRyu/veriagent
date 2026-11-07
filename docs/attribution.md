# Attribution

## VeriTrans

VeriAgent grew out of exploring VeriTrans's approach to language-to-logic translation and symbolic checking. The source implementation here uses its own bounded AST, SymPy CNF compilation and PySAT evaluation around owner-reviewed sharing policies.

VeriTrans is credited to Xuan Liu, Dheeraj Kodakandla, Kushagra Srivastva and Mahfuza Farooque. [Paper](https://arxiv.org/abs/2604.10341) · [Conference record](https://2026.aiwareconf.org/details/aiware-2026-papers/21/VeriTrans-Fine-Tuned-LLM-Assisted-NL-PL-Translation-via-a-Deterministic-Neuro-symbol)

## Dependencies

Runtime dependencies include Python, Qt for Python/PySide6 Essentials and Shiboken6, the MCP Python SDK, Pydantic, SymPy and python-sat with MiniSat. Tests use pytest. Versions are pinned in the requirements files and project metadata.

Original VeriAgent code uses the [MIT license](../LICENSE). Dependencies retain their upstream licenses and notices. [Third-party notices](third-party-notices.md)
