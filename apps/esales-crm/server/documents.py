from pathlib import Path
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

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


def render_receipt(snapshot, output, font_path=FONT, semibold_path=SEMIBOLD_FONT):
    pdfmetrics.registerFont(TTFont('ReceiptCJK',str(font_path)))
    pdfmetrics.registerFont(TTFont('ReceiptCJK-Semibold',str(semibold_path)))
    regular=ParagraphStyle('receipt',fontName='ReceiptCJK',fontSize=10,leading=15,textColor=colors.HexColor('#171315'),wordWrap='CJK')
    bold=ParagraphStyle('emphasis',parent=regular,fontName='ReceiptCJK-Semibold')
    small=ParagraphStyle('small',parent=regular,fontSize=9,leading=13)
    column_header=ParagraphStyle('column_header',parent=small,fontName='ReceiptCJK-Semibold',textColor=colors.HexColor('#651426'))
    title=ParagraphStyle('title',parent=regular,fontSize=25,leading=34,textColor=colors.HexColor('#881c32'))
    right=ParagraphStyle('right',parent=regular,alignment=TA_RIGHT)
    bold_right=ParagraphStyle('bold_right',parent=bold,alignment=TA_RIGHT)
    def para(value,style=regular):return Paragraph(escape(str(value or '')).replace('\n','<br/>'),style)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    issuer=snapshot['issuer'];billing=snapshot['billing']
    story=[para(issuer['name'],bold),para(issuer.get('address',''),small),para(issuer.get('email',''),small),Spacer(1,18),para('SALES RECEIPT',title),Spacer(1,16)]
    header=Table([[para('BILL TO\n'+billing['billing_name']+'\n'+billing.get('address','')),para('RECEIPT NO. '+snapshot['number']+'\nDATE '+snapshot['issued_date']+'\nORDER '+snapshot['reference'],right)]],colWidths=[290,220],splitInRow=1)
    header.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0)]));story.extend([header,Spacer(1,24)])
    rows=[[para(x,column_header) for x in ['DESCRIPTION','QTY','RATE','AMOUNT']]]
    for line in snapshot['lines']:
        rows.append([para(line['description']),para(line['quantity'],right),para(amount(line['unit_minor']),right),para(amount(line['amount_minor']),right)])
    table=Table(rows,colWidths=[310,40,75,85],repeatRows=1,hAlign='LEFT',splitInRow=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f0dde0')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#ab2843')),('LINEBELOW',(0,1),(-1,-1),0.3,colors.HexColor('#ddd4cd'))]))
    story.extend([table,Spacer(1,14)])
    totals=[['SUBTOTAL',amount(snapshot['subtotal_minor'])],['DISCOUNT',amount(snapshot['discount_minor'])],['TAX',amount(snapshot['tax_minor'])],['AMOUNT PAID',snapshot['currency']+' '+amount(snapshot['total_minor'])],['BALANCE DUE',snapshot['currency']+' 0.00']]
    tt=Table([[para(a,bold if a in ('AMOUNT PAID','BALANCE DUE') else regular),para(b,bold_right if a in ('AMOUNT PAID','BALANCE DUE') else right)] for a,b in totals],colWidths=[310,200]);tt.setStyle(TableStyle([('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    note='Payment: '+snapshot['payment_method']
    if snapshot['kind']=='renewal' and snapshot.get('service_end'):note+='\nService end: '+snapshot['service_end']
    if snapshot.get('coupon'):note+='\nDiscount code: '+snapshot['coupon']
    story.append(KeepTogether([tt,Spacer(1,18),para(note,small)]))
    def footer(canvas,doc):
        if doc.page==1:
            logo=Path(__file__).resolve().parent.parent/'assets/converto-logo.png'
            if logo.exists():canvas.drawImage(str(logo),500,692,width=55,height=55,mask='auto')
        canvas.setFont('ReceiptCJK',8);canvas.setFillColor(colors.HexColor('#47353a'));canvas.drawString(48,30,snapshot['number']);canvas.drawRightString(564,30,f'Page {doc.page}')
    SimpleDocTemplate(str(output),pagesize=letter,leftMargin=48,rightMargin=48,topMargin=42,bottomMargin=48,title='Sales receipt '+snapshot['number'],author=issuer['name']).build(story,onFirstPage=footer,onLaterPages=footer)
    output.chmod(0o600)
