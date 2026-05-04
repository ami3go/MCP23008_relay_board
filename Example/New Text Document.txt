
from resistor_selector import ResistorSelector

rs = ResistorSelector(num_boards=3)

rs.open()
rs.select(board=1, resistance="68k")
rs.select(board=2, resistance="1.6k")

print(rs.status())

rs.close()
