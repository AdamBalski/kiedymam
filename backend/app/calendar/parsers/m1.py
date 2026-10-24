from app.calendar.parsers.matrix import parse_matrix_workbook


class M1Parser:
    format_id = "M1"

    def parse(self, contents: bytes) -> dict:
        return parse_matrix_workbook(contents, self.format_id)
