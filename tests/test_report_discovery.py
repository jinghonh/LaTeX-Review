import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from latex_review.serve import create_server


def test_report_discovery_works_without_annotation_content_and_checks_origin(tmp_path):
    (tmp_path / 'report.html').write_text('<html>Report</html>', encoding='utf-8')
    with create_server(tmp_path) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            result = json.load(urlopen(base + '/api/report'))
            assert result['report_directory'] == str(tmp_path.resolve())
            assert result['report_url'] == base + '/report.html'
            assert 'annotation_content_error' in result
            assert 'token' not in result
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + '/api/report', headers={'Origin': 'https://example.org'}))
            assert error.value.code == 403
        finally:
            server.shutdown()
            worker.join()
