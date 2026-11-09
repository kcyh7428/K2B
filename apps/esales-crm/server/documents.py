from pathlib import Path
from datetime import date
import json
import os
import fcntl
import tempfile
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

FONT = Path(__file__).resolve().parent.parent/'assets/NotoSansTC.ttf'
SEMIBOLD_FONT = FONT.with_name('NotoSansTC-Semibold.ttf')


def ensure_pdf(store,receipt_id,data_dir):
    # Serialize font rendering across processes without holding a database writer.
    folder=Path(data_dir)/'receipts';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(folder/'.render.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX)
        row=store.one('SELECT * FROM receipts WHERE id=?',(receipt_id,))
        if not row:raise ValueError('Receipt not found')
        if row['pdf_path']:
            path=Path(row['pdf_path'])
            if not path.is_file():raise ValueError('Stored receipt PDF is missing; restore it from backup')
            return path
        snapshot=json.loads(row['snapshot'])
        if snapshot.get('legacy'):raise ValueError('Original legacy PDF has not been imported; it will not be recreated')
        path=folder/(receipt_id+'.pdf')
        temporary_fd,temporary_name=tempfile.mkstemp(prefix=receipt_id+'-',suffix='.tmp',dir=folder)
        os.close(temporary_fd);temporary=Path(temporary_name)
        try:
            render_receipt(snapshot,temporary)
            temporary.chmod(0o600)
            with store.transaction() as c:
                current=c.execute('SELECT pdf_path FROM receipts WHERE id=?',(receipt_id,)).fetchone()
                if current['pdf_path']:
                    existing=Path(current['pdf_path'])
                    if not existing.is_file():raise ValueError('Stored receipt PDF is missing; restore it from backup')
                    return existing
                os.replace(temporary,path)
                c.execute("UPDATE receipts SET pdf_path=?,state=CASE WHEN state='void' THEN 'void' ELSE 'ready' END WHERE id=?",(str(path),receipt_id))
        finally:temporary.unlink(missing_ok=True)
        return path
    finally:os.close(descriptor)


def amount(value):
    return f'{value//100:,}.{value%100:02d}'


def render_receipt(snapshot, output, font_path=FONT, semibold_path=SEMIBOLD_FONT, *, preview=False):
    pdfmetrics.registerFont(TTFont('ReceiptCJK', str(font_path)))
    pdfmetrics.registerFont(TTFont('ReceiptCJK-Semibold', str(semibold_path)))
    red = colors.HexColor('#870024')
    regular = ParagraphStyle('receipt', fontName='ReceiptCJK', fontSize=10, leading=14,
                             textColor=colors.HexColor('#171315'), wordWrap='CJK')
    bold = ParagraphStyle('emphasis', parent=regular, fontName='ReceiptCJK-Semibold')
    small = ParagraphStyle('small', parent=regular, fontSize=9, leading=13)
    column_header = ParagraphStyle('column_header', parent=small, textColor=red)
    numeric_header = ParagraphStyle('numeric_header', parent=column_header, alignment=TA_RIGHT)
    title = ParagraphStyle('title', parent=regular, fontSize=21, leading=29, textColor=red)
    right = ParagraphStyle('right', parent=regular, alignment=TA_RIGHT)
    numeric = ParagraphStyle('numeric', parent=small, alignment=TA_RIGHT)
    balance = ParagraphStyle('balance', parent=bold, fontSize=15, leading=19, alignment=TA_RIGHT)

    def escaped_text(value):
        return escape('' if value is None else str(value)).replace('\n', '<br/>')

    def para(value, style=regular):
        return Paragraph(escaped_text(value), style)

    def field(label, value, style=small):
        return Paragraph(f'<font name="ReceiptCJK-Semibold">{label}</font> {escaped_text(value)}', style)

    def display_date(value):
        if not value:
            return ''
        try:
            return date.fromisoformat(str(value)).strftime('%d/%m/%Y')
        except ValueError:
            return str(value)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    issuer, billing = snapshot['issuer'], snapshot['billing']
    issued_date = display_date(snapshot['issued_date'])
    story = [para(issuer['name'], bold), para(issuer.get('address', '')),
             para(issuer.get('email', '')), Spacer(1, 6), para('SALES RECEIPT', title), Spacer(1, 12)]
    header = Table([[
        [para('BILL TO', bold), para(billing['billing_name']), para(billing.get('address', ''))],
        [field('SALES NO.', snapshot['number'], right), field('DATE', issued_date, right),
         field('ORDER NO.', snapshot['reference'], right)],
    ]], colWidths=[294, 224], minRowHeights=[80], splitInRow=1)
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story.extend([header, Spacer(1, 12), HRFlowable(width='100%', thickness=.5, color=red), Spacer(1, 32)])

    activities = {'monthly': 'e-Sales - Renewal Subscription', 'etoken': 'e-Sales - E-token Top-up'}
    rows = [[para(label, column_header) for label in ('ACTIVITY', 'DESCRIPTION')]
            + [para(label, numeric_header) for label in ('QTY', 'RATE', 'AMOUNT')]]
    for line in snapshot['lines']:
        rows.append([para(activities.get(line.get('type'), 'e-Sales'), bold),
                     para(line['description']), para(line['quantity'], numeric),
                     para(amount(line['unit_minor']), numeric), para(amount(line['amount_minor']), numeric)])
    table = Table(rows, colWidths=[104, 220, 36, 78, 80], repeatRows=1,
                  hAlign='LEFT', splitInRow=1)
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5ccd1')),
                              ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('LEFTPADDING', (2, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 1), (-1, -1), 6),
                              ('BOTTOMPADDING', (0, 1), (-1, -1), 8)]))
    story.extend([table, Spacer(1, 10)])

    totals = [('SUBTOTAL', amount(snapshot['subtotal_minor']))]
    if snapshot['discount_minor']:
        totals.append(('DISCOUNT', amount(snapshot['discount_minor'])))
    currency = snapshot['currency']
    totals.extend([('TAX', amount(snapshot['tax_minor'])),
                   ('TOTAL', f"{currency} {amount(snapshot['total_minor'])}"),
                   ('BALANCE DUE', f'{currency} 0.00')])
    total_rows = [[para(''), para(label), para(value, balance if label == 'BALANCE DUE' else right)]
                  for label, value in totals]
    totals_table = Table(total_rows, colWidths=[260, 131, 127], hAlign='LEFT')
    totals_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                     ('TOPPADDING', (0, 0), (-1, -1), 0),
                                     ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    notes = [field('Payment method', snapshot['payment_method'])]
    if snapshot['kind'] == 'renewal' and snapshot.get('service_end'):
        notes.append(field('Service end date', display_date(snapshot['service_end'])))
    if snapshot.get('coupon'):
        notes.append(field('Discount code', snapshot['coupon']))
    story.append(KeepTogether([HRFlowable(width='100%', thickness=.6, color=colors.HexColor('#999999'),
                                         dash=(1, 2)), Spacer(1, 6), totals_table, Spacer(1, 14), *notes]))

    def page_marks(canvas, doc):
        if doc.page == 1:
            logo = Path(__file__).resolve().parent.parent/'assets/converto-logo.png'
            if logo.exists():
                canvas.drawImage(str(logo), 509, 709, width=42, height=42, mask='auto')
        canvas.setFont('ReceiptCJK', 8)
        canvas.setFillColor(colors.HexColor('#47353a'))
        if preview:
            canvas.drawString(47, 30, 'PREVIEW ONLY - NOT ISSUED')
        elif snapshot.get('environment') in ('staging', 'demo'):
            canvas.drawString(47, 30, 'TEST RECEIPT')
        canvas.drawRightString(565, 30, f"{snapshot['number']} / Page {doc.page}")

    doc = SimpleDocTemplate(str(output), pagesize=letter, leftMargin=41, rightMargin=41,
                            topMargin=35, bottomMargin=48, title='Sales receipt '+snapshot['number'],
                            author=issuer['name'])
    doc.build(story, onFirstPage=page_marks, onLaterPages=page_marks)
    output.chmod(0o600)
