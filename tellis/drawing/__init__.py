"""2D dimensioned drawing -> exact SolidWorks part.

    drawing ─┬─ linework.py   trace the thick part lines -> closed loops (mm)   (shape)
             └─ dimensions.py VLM reads the dimension values                    (numbers)
                     fit.py        loops -> lines + arcs, snapped to the dimensions, mirrored exactly
                     build.py      -> stage3 cad_plan (plate, relief, through-cuts) -> SolidWorks
"""
