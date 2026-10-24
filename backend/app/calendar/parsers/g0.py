from app.calendar.parser import parse_g0_workbook


class G0Parser:
    format_id = "G0"

    def parse(self, contents: bytes) -> dict:
        return parse_g0_workbook(contents)
