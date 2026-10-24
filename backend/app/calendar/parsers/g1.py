from app.calendar.parsers.matrix import parse_matrix_workbook


class G1Parser:
    format_id = "G1"

    def parse(self, contents: bytes) -> dict:
        return parse_matrix_workbook(contents, self.format_id)
