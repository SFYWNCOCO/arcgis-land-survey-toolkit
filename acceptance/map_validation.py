"""导出图件的轻量读回检查；业务符合性仍需在缩略图中人工复核。"""
from __future__ import annotations

import os
import hashlib
import math
import json
import datetime


def audit_map_scale(expected_scale, extent, width_cm, height_cm, parcel_bounds, meters_per_unit=1):
    """Measure the displayed ground width/height against physical page dimensions."""
    values = list(extent) + list(parcel_bounds) + [expected_scale, width_cm, height_cm, meters_per_unit]
    if not all(math.isfinite(float(v)) for v in values) or min(expected_scale,width_cm,height_cm,meters_per_unit)<=0:
        return {'status':'FAIL','detail':'比例尺或图框尺寸无效'}
    x0,y0,x1,y1=extent
    sx=(x1-x0)*meters_per_unit/(width_cm/100)
    sy=(y1-y0)*meters_per_unit/(height_cm/100)
    a,b,c,d=parcel_bounds
    coverage=x0<=a<=c<=x1 and y0<=b<=d<=y1
    error=max(abs(sx/expected_scale-1),abs(sy/expected_scale-1))
    return {'status':'PASS' if coverage and error<.001 else 'FAIL',
            'nominal_scale':expected_scale,'measured_scale_x':sx,'measured_scale_y':sy,
            'relative_error':error,'covers_planned_bounds':coverage,
            'frame_extent':list(extent),'frame_cm':[width_cm,height_cm]}


def validate_visual_review(image_path, render_key, receipt=None):
    """A decoded thumbnail is not a visual review; bind real review evidence to this render."""
    result={'status':'NEEDS_REVIEW','receipt_path':os.fspath(image_path)+'.visual-review.json',
            'render_key':render_key,'detail':'尚无当前图件的视觉复核回执'}
    try:
        if not isinstance(render_key,str) or not render_key.strip():
            raise ValueError('缺少当前渲染指纹')
        if verify_export_artifact(image_path).get('status')=='FAIL':
            raise ValueError('当前审图图片无法完整解码')
        with open(image_path,'rb') as stream:
            result['image_sha256']=hashlib.file_digest(stream,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(stream.read()).hexdigest()
        if receipt is None:
            with open(result['receipt_path'],encoding='utf-8') as stream:receipt=json.load(stream)
        if not isinstance(receipt,dict) or receipt.get('image_sha256')!=result['image_sha256'] or receipt.get('render_key')!=render_key:
            raise ValueError('图像或渲染指纹已改变，须重新审图')
        reviewer=receipt.get('reviewer') or {}
        if receipt.get('image_input_verified') is not True or not isinstance(reviewer,dict) or reviewer.get('kind') not in ('vision_model','human') or not isinstance(reviewer.get('name'),str) or not reviewer['name'].strip():
            raise ValueError('缺少实际图片输入或真实审查者证据')
        stamp=datetime.datetime.fromisoformat(str(receipt.get('reviewed_at') or '').replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp>datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=5):
            raise ValueError('审图时间无效')
        findings=receipt.get('findings')
        if not isinstance(findings,list) or not 1<=len(findings)<=32 or any(not isinstance(x,dict) or not isinstance(x.get('item'),str) or not x['item'].strip() or not isinstance(x.get('detail'),str) or not x['detail'].strip() or x.get('status') not in ('PASS','FAIL','NEEDS_REVIEW') for x in findings):
            raise ValueError('审图明细无效')
        states={x['status'] for x in findings}
        aggregate='FAIL' if 'FAIL' in states else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in states else 'PASS'
        if receipt.get('status')!=aggregate:raise ValueError('审图总状态与明细矛盾')
        result.update(status=aggregate,detail='已校验当前图像与渲染指纹的视觉回执',reviewer=reviewer,findings=findings,reviewed_at=receipt['reviewed_at'])
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result['detail']=str(exc) if not isinstance(exc,FileNotFoundError) else result['detail']
    return result


MAGIC = {
    "jpg": (b"\xff\xd8", b"\xff\xd9"),
    "jpeg": (b"\xff\xd8", b"\xff\xd9"),
    "png": (b"\x89PNG\r\n\x1a\n", None),
    "pdf": (b"%PDF-", b"%%EOF"),
}


def write_atlas_bundle(exported, directory):
    """Build a readable gallery and merge verified per-sheet PDFs without changing review status."""
    import html
    from urllib.parse import quote
    from pathlib import Path
    rows = exported.get('outputs') or []
    if not rows:
        raise ValueError('图件清单为空，不能生成整套PDF')
    for row in rows:
        for ext in ('pdf', 'jpg', 'png'):
            checked = verify_export_artifact(row.get(ext, ''), ext)
            if checked['status'] == 'FAIL' or (ext == 'pdf' and checked.get('pages') != 1):
                raise ValueError('图件文件无效：' + str(row.get(ext)))
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / '整套图件.pdf'
    staged = root / ('.atlas-' + __import__('uuid').uuid4().hex + '.pdf')
    try:
        try:
            import fitz
        except ImportError:
            import arcpy.mp
            document = arcpy.mp.PDFDocumentCreate(str(staged))
            try:
                for row in rows: document.appendPages(row['pdf'])
                document.saveAndClose()
            finally:
                del document
        else:
            with fitz.open() as document:
                for row in rows:
                    with fitz.open(row['pdf']) as part: document.insert_pdf(part)
                document.save(str(staged), garbage=3, deflate=True)
        check = verify_export_artifact(str(staged), 'pdf', expected={'pages': len(rows)})
        if check['status'] == 'FAIL' or check.get('pages') != len(rows):
            raise ValueError('整套PDF页数或文件检查失败')
        os.replace(str(staged), str(pdf))
    finally:
        if staged.exists(): staged.unlink()
    def url(path):
        return quote(os.path.relpath(os.path.abspath(path), str(root)).replace(os.sep, '/'), safe='/')
    cards = []
    for row in rows:
        cards.append('<article><h2>{}</h2><a href="{}"><img loading="lazy" src="{}"></a>'
                     '<p><a href="{}">PDF</a> · <a href="{}">PNG</a> · 1:{:g}</p></article>'.format(
                         html.escape(str(row.get('target') or '图件')), url(row['jpg']), url(row.get('thumbnail') or row['jpg']),
                         url(row['pdf']), url(row['png']), float(row.get('scale') or 0)))
    gallery = root / '图件目录.html'
    page = ('<!doctype html><meta charset="utf-8"><title>整套图件</title>'
            '<style>body{{font:16px sans-serif;margin:24px;background:#eef2f5;color:#172c40}}'
            'main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:18px}}'
            'article{{background:white;padding:12px}}img{{width:100%}}h2{{font-size:18px}}</style>'
            '<h1>整套图件</h1><p><a href="{}">整套PDF · {}幅</a> · {}</p><main>{}</main>').format(
                url(pdf), len(rows), html.escape(str(exported.get('status', 'NEEDS_REVIEW'))), ''.join(cards))
    gallery.write_text(page, encoding='utf-8')
    return {'pdf': str(pdf), 'html': str(gallery), 'pages': len(rows), 'status': 'PASS'}


def _contract_checks(result, expected):
    if not expected or result['status'] == 'FAIL':
        return result
    checks = result.setdefault('checks', [])
    def check(item, status, detail):
        checks.append(dict(item=item, status=status, detail=detail))
    if result['kind'] in ('jpg', 'jpeg', 'png'):
        if all(expected.get(key) for key in ('width_mm', 'height_mm', 'dpi')):
            wanted = [expected['width_mm'] / 25.4 * expected['dpi'], expected['height_mm'] / 25.4 * expected['dpi']]
            ok = all(abs(actual - target) <= 2 for actual, target in zip((result['width'], result['height']), wanted))
            check('图像像素尺寸', 'PASS' if ok else 'FAIL', dict(expected=wanted, actual=[result['width'], result['height']]))
        if expected.get('dpi'):
            actual = result.get('dpi')
            check('DPI元数据', 'NEEDS_REVIEW' if not actual else 'PASS' if all(abs(float(v) - expected['dpi']) <= 1 for v in actual) else 'FAIL',
                  dict(expected=expected['dpi'], actual=actual))
    elif result['kind'] == 'pdf':
        if expected.get('pages'):
            check('PDF页数', 'PASS' if result.get('pages') == expected['pages'] else 'FAIL',
                  dict(expected=expected['pages'], actual=result.get('pages')))
        if expected.get('width_mm') and expected.get('height_mm'):
            sizes = result.get('page_sizes_mm')
            ok = sizes and all(abs(pair[0] - expected['width_mm']) <= .5 and abs(pair[1] - expected['height_mm']) <= .5 for pair in sizes)
            check('PDF纸面尺寸', 'NEEDS_REVIEW' if not sizes else 'PASS' if ok else 'FAIL',
                  dict(expected=[expected['width_mm'], expected['height_mm']], actual=sizes))
    statuses = {row['status'] for row in checks} | {result['status']}
    result['status'] = 'FAIL' if 'FAIL' in statuses else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in statuses else 'PASS'
    return result


def verify_export_artifact(path, kind=None, min_bytes=64, expected=None):
    """检查存在性、容器签名，并在可用时实际解码图像/PDF。"""
    path = os.fspath(path)
    kind = (kind or os.path.splitext(path)[1].lstrip(".")).lower()
    result = {"path": path, "kind": kind, "status": "FAIL", "method": "signature"}
    if kind not in MAGIC:
        result["detail"] = "不支持的成果格式：{}".format(kind)
        return result
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(16)
            fh.seek(max(0, size - 2048))
            tail = fh.read()
    except OSError as exc:
        result["detail"] = "文件无法读取：{}".format(exc)
        return result
    result["bytes"] = size
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    result["sha256"] = digest.hexdigest()
    if size < int(min_bytes):
        result["detail"] = "文件过小或为空"
        return result
    first, last = MAGIC[kind]
    if not head.startswith(first) or (last and last not in tail):
        result["detail"] = "文件格式签名不完整或损坏"
        return result

    if kind in ("jpg", "jpeg", "png"):
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                actual_dpi = image.info.get('dpi')
                image.load()
            if width <= 0 or height <= 0:
                result["detail"] = "图像尺寸无效"
                return result
            result.update({"status": "PASS", "method": "Pillow decode", "width": width,
                           "height": height, "detail": "图像可重新打开并解码"})
            result['dpi'] = list(actual_dpi) if actual_dpi else None
            return _contract_checks(result, expected)
        except ImportError:
            result.update({"status": "NEEDS_REVIEW", "detail": "缺少图像解码器；签名通过，需人工打开核查"})
            return result
        except Exception as exc:  # noqa: BLE001
            result["detail"] = "图像解码失败：{}".format(type(exc).__name__)
            return result

    try:
        import fitz
        with fitz.open(path) as document:
            page_count = document.page_count
            result['page_sizes_mm'] = [[page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72] for page in document]
        if page_count < 1:
            result["detail"] = "PDF 没有页面"
            return result
        result.update({"status": "PASS", "method": "PyMuPDF open", "pages": page_count,
                       "detail": "PDF 可重新打开，{} 页".format(page_count)})
    except ImportError:
        try:
            import arcpy
            import arcpy.mp
            document = arcpy.mp.PDFDocumentOpen(path)
            page_count = document.pageCount
            del document
            if page_count < 1:
                result["detail"] = "PDF 没有页面"
            else:
                result.update({"status": "PASS", "method": "ArcPy PDFDocumentOpen", "pages": page_count,
                               "detail": "PDF 可重新打开，{} 页".format(page_count)})
        except ImportError:
            result.update({"status": "NEEDS_REVIEW", "detail": "缺少 PDF 读取器；签名通过，需人工打开核查"})
        except Exception as exc:
            result["detail"] = "ArcPy PDF 重新打开失败：{}".format(type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        result["detail"] = "PDF 重新打开失败：{}".format(type(exc).__name__)
    return _contract_checks(result, expected)


def collect_layout_snapshot(layout):
    """只读 CIM 以取得锚点/文字形状；不调用 setDefinition 或 save。"""
    definitions = {}
    try:
        def visit(elements):
            for element in elements:
                definitions[element.name] = element
                visit(getattr(element, 'elements', []) or [])
        visit(layout.getDefinition('V3').elements)
    except Exception:
        pass
    rows = []
    for element in layout.listElements():
        definition = definitions.get(element.name)
        graphic = getattr(definition, 'graphic', None)
        shape = getattr(graphic, 'shape', None)
        if isinstance(shape, dict):
            text_geometry = 'point' if 'x' in shape else 'paragraph'
        else:
            text_geometry = 'point' if hasattr(shape, 'x') else 'paragraph' if shape else None
        rows.append(dict(name=element.name, type=element.type, visible=getattr(element, 'visible', True),
            x=getattr(element, 'elementPositionX', None), y=getattr(element, 'elementPositionY', None),
            width=getattr(element, 'elementWidth', None), height=getattr(element, 'elementHeight', None),
            anchor=getattr(definition, 'anchor', None), rotation=getattr(element, 'elementRotation', 0),
            text=getattr(element, 'text', None), text_geometry=text_geometry,
            overflowing=getattr(element, 'isOverflowing', None)))
    return dict(width=layout.pageWidth, height=layout.pageHeight, units=layout.pageUnits, elements=rows)


def _element_bounds(row):
    anchors = {'TopLeftCorner': (0, 1), 'TopMidPoint': (.5, 1), 'TopRightCorner': (1, 1),
        'CenterPoint': (.5, .5), 'LeftMidPoint': (0, .5), 'RightMidPoint': (1, .5),
        'BottomLeftCorner': (0, 0), 'BottomMidPoint': (.5, 0), 'BottomRightCorner': (1, 0)}
    if row.get('anchor') not in anchors or any(row.get(key) is None for key in ('x', 'y', 'width', 'height')):
        return None
    ax, ay = anchors[row['anchor']]
    width, height, x, y = (float(row[key]) for key in ('width', 'height', 'x', 'y'))
    angle = math.radians(float(row.get('rotation') or 0))
    corners = [(dx * math.cos(angle) - dy * math.sin(angle) + x,
                dx * math.sin(angle) + dy * math.cos(angle) + y)
               for dx in (-ax * width, (1 - ax) * width) for dy in (-ay * height, (1 - ay) * height)]
    return [min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners), max(p[1] for p in corners)]


def audit_layout_snapshot(snapshot):
    checks, obstacles = [], []
    width, height = snapshot.get('width'), snapshot.get('height')
    if not width or not height:
        return dict(status='NEEDS_REVIEW', checks=[dict(item='纸面尺寸', status='NEEDS_REVIEW', detail='无法读取布局尺寸')])
    for row in snapshot.get('elements', []):
        if not row.get('visible', True):
            continue
        kind, name = row.get('type'), row.get('name', '')
        if kind in ('GRAPHIC_ELEMENT', 'GROUP_ELEMENT'):
            continue
        bounds = _element_bounds(row)
        if bounds is None:
            checks.append(dict(item='元素边界', status='NEEDS_REVIEW', elements=[name], detail='缺少锚点或尺寸'))
        elif bounds[0] < -.02 or bounds[1] < -.02 or bounds[2] > width + .02 or bounds[3] > height + .02:
            checks.append(dict(item='纸面边界', status='FAIL', elements=[name], bounds=bounds, detail='元素超出纸面'))
        if kind == 'TEXT_ELEMENT':
            overflowing = row.get('overflowing')
            if overflowing is True or (overflowing is None and row.get('text_geometry') != 'point'):
                checks.append(dict(item='文字溢出', status='FAIL' if overflowing else 'NEEDS_REVIEW',
                    elements=[name], detail='文字框溢出' if overflowing else '无法核定段落文字是否完整，需看图'))
        if bounds is not None and name not in ('WEBMAP_MAP_FRAME', '外图廓') and kind in ('TEXT_ELEMENT', 'LEGEND_ELEMENT', 'MAPSURROUND_ELEMENT', 'MAPFRAME_ELEMENT'):
            obstacles.append((name, bounds))
    for index, (name, a) in enumerate(obstacles):
        for other, b in obstacles[index + 1:]:
            if name == other:
                continue
            ix, iy = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
            if ix > .02 and iy > .02:
                checks.append(dict(item='版面压盖', status='NEEDS_REVIEW', elements=[name, other],
                    detail='元素包围框相交，需确认文字/符号是否实际遮挡', overlap=[ix, iy]))
    if not checks:
        checks.append(dict(item='布局几何检查', status='PASS', detail='可读元素在纸面内，无包围框相交；不替代逐图看图'))
    statuses = {row['status'] for row in checks}
    return dict(status='FAIL' if 'FAIL' in statuses else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in statuses else 'PASS',
                checks=checks, page=dict(width=width, height=height, units=snapshot.get('units')))


def layout_export_contract(layout, dpi):
    factor = {'CENTIMETER': 10, 'MILLIMETER': 1, 'INCH': 25.4, 'POINT': 25.4 / 72}.get(str(layout.pageUnits).upper())
    return dict(width_mm=layout.pageWidth * factor, height_mm=layout.pageHeight * factor, dpi=int(dpi), pages=1) if factor else dict(dpi=int(dpi), pages=1)


def create_thumbnail(source, destination, max_edge=640):
    """由复核用 PNG/JPG 生成等比例缩略图。"""
    from PIL import Image
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    with Image.open(source) as image:
        image.thumbnail((int(max_edge), int(max_edge)))
        image.convert("RGB").save(destination, format="JPEG", quality=85, optimize=True)
    return destination
