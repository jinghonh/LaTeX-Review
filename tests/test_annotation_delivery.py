import json
import re
from pathlib import Path
from latex_review.annotations import AnnotationStore, load_content
from latex_review.delivery import publication_html
from test_annotation_delivery_browser import live_report


def payload(html,id):
    return json.loads(re.search(rf'<script type="application/json" id="{id}">(.*?)</script>',html,re.S)[1])


def test_publication_refreshes_latest_backup(live_report):
    root,entry,output,source=live_report
    content=load_content(output);store=AnnotationStore.for_content(content);u=next(u for u in content['units'] if u['kind']=='text')
    store.dispatch(content,{'operation':'create','expected_revision':0,'selection':{'unit_id':u['id'],'start':0,'end':6},'body':'latest','author':{'name':'A','type':'user'},'category':'note'})
    html=publication_html(output);assert len(payload(html,'annotation-backup')['threads'])==1
    assert payload(html,'annotation-content')==content


def test_missing_project_does_not_create_paths(live_report,tmp_path):
    from latex_review.delivery import embed_annotations
    from latex_review.annotations import _hash
    root,entry,output,source=live_report;content=load_content(output)
    missing=tmp_path/'missing-project';content['project_root']=str(missing);content['project_id']=_hash({'root':str(missing),'entry':content['entry']})
    html=embed_annotations('<html><head></head><body></body></html>',content)
    assert not missing.exists() and payload(html,'annotation-backup')['threads']=={}


def test_publication_retains_portable_backup_without_local_project(live_report,tmp_path):
    from latex_review.delivery import embed_annotations
    import shutil
    root,entry,output,source=live_report
    c=load_content(output);store=AnnotationStore.for_content(c);u=next(u for u in c['units'] if u['kind']=='text')
    store.dispatch(c,{'operation':'create','expected_revision':0,'selection':{'unit_id':u['id'],'start':0,'end':6},'body':'portable','author':{'name':'A','type':'user'},'category':'note'})
    html=publication_html(output);(output/'report.html').write_text(html)
    shutil.rmtree(root)
    republished=publication_html(output)
    assert len(payload(republished,'annotation-backup')['threads'])==1 and not root.exists()


def test_directory_report_keeps_large_asset_link(live_report,monkeypatch):
    from latex_review import delivery
    root,entry,output,source=live_report
    assets=output/'assets';assets.mkdir();(assets/'image.png').write_bytes(b'PNG')
    monkeypatch.setattr(delivery,'_MAX_EMBEDDED_ASSET',1)
    html=delivery.embed_annotations('<html><head></head><body><img src="assets/image.png"></body></html>',load_content(output),output)
    assert 'src="assets/image.png"' in html and payload(html,'annotation-assets')=={}
    import pytest
    with pytest.raises(delivery.DeliveryError):delivery.single_file_html(html,output)
