"""Tellis: 2D image -> TRELLIS.2 -> 3D model -> SolidWorks (SLDPRT / STEP).

    IMAGE ── TRELLIS.2 (trellis_client.py) ── 3D model (mesh_prep.py) ── SolidWorks (solidworks_import.py)
          ── backend (server.py) ── frontend (static/, interactive 3D)

Run from the repository root:  python -m tellis serve   |   python -m tellis run --image drawing.png --size-mm 120
"""
