# Third-party distribution considerations

Dependencies are installed separately from the source in this repository. Their versions are pinned in the requirements files and project metadata. Their upstream licenses and notices continue to apply.

A compiled Windows distribution would include Python and components such as Qt for Python (PySide6 Essentials and Shiboken6), the MCP SDK and its dependencies, Pydantic, SymPy, python-sat, and MiniSat. Review the versions, applicable licenses, and required notice material for each binary release.

Qt for Python has LGPL/GPL/commercial license options. PyInstaller and Inno Setup have separate packaging terms. No compiled installer is included. [Attribution](attribution.md) · [Project license](../LICENSE)
