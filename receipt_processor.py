"""
レシート自動処理ツール
PDF分割 → Vision API OCR → カレンダー照合 → スプレッドシート出力
"""

import os
import json
import base64
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import random
import fitz  # PyMuPDF
import requests
from PIL import Image
import io

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ==================== 設定 ====================

CONFIG = {
    # Gemini APIキー（環境変数 GEMINI_API_KEY から取得）
    'GEMINI_API_KEY': os.environ['GEMINI_API_KEY'],

    # Google DriveフォルダID
    'INPUT_FOLDER_ID':  '1lCLYC-UJAaVOd_zx89IiywDSYwVTCRD5',
    'OUTPUT_FOLDER_ID': '1f7bM847cDMmSu_1JUz7qYqrkQSKxhV5N',
    'DONE_FOLDER_ID':   '1T7esr7O1O8R563ijA_eoFIVur8DZXrYZ',

    # スプレッドシートID
    'SPREADSHEET_ID': '1dvGGzxrkEYQtTAOIytS5Co9U_RXBWx8ODjUwhYcvX10',

    # カレンダーID（GmailアドレスでOK）
    'CALENDAR_ID': 'nobutai.2327@gmail.com',

    # 確定申告の対象年度（例：2025年分なら2025）
    'TAX_YEAR': 2026,

    # カレンダー照合の時間幅（前後何時間）
    'CALENDAR_SEARCH_HOURS': 6,
}

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
]

CREDENTIALS_FILE = Path.home() / 'credentials.json'
TOKEN_FILE = Path.home() / 'token.json'

NEEDS_REVIEW_NAMES = ['角田先生', '三浦先生', '永井先生', '石川先生']

# ==================== 認証 ====================

def get_google_services():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())

    drive = build('drive', 'v3', credentials=creds)
    calendar = build('calendar', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, calendar, sheets

# ==================== PDF分割 ====================

def split_pdf_to_images(pdf_path):
    """PDFを1ページずつPIL Imageに変換"""
    doc = fitz.open(pdf_path)
    images = []
    print(f"  ページ数: {doc.page_count}")
    for i, page in enumerate(doc):
        mat = fitz.Matrix(2.0, 2.0)  # 2倍解像度
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("jpeg")
        images.append((i + 1, img_data))
    doc.close()
    return images

# ==================== OCR ====================

def ocr_images_batch(pages):
    """全ページをまとめて1リクエストでGeminiに送る"""
    prompt = """複数のレシート・領収書画像が渡されます。
各画像について情報を抽出し、必ずJSON配列のみで返してください。マークダウンや説明文は不要です。
画像の順番通りに配列に格納してください。

[
  {
    "date": "月日のみMM-DD形式。例：02-27。読み取れない場合はnull",
    "store_name": "施設名・店名をそのまま。「領収書」「領収証」は除く。例：名鉄協商パーキング 丸の内PB、名古屋法務局 岡崎支局",
    "amount": 合計金額の数値（税込・円・数値のみ）,
    "account_item": "旅費交通費/接待交際費/租税公課/消耗品費/要確認のいずれか"
  }
]

勘定科目の判定：
- 駐車場・パーキング・駐輪場・電車・タクシー・交通機関 → 旅費交通費
- 飲食店・カフェ・懇親会・会食 → 接待交際費
- 法務局・収入印紙・登記・印紙 → 租税公課
- 文具・消耗品 → 消耗品費
- 不明 → 要確認

注意：金額は合計・請求金額・現金欄を優先。"""

    parts = []
    for page_num, img_bytes in pages:
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    parts.append({"text": prompt})

    payload = {"contents": [{"parts": parts}]}

    for attempt in range(1, 11):
        try:
            print(f"  Gemini API送信中（{len(pages)}枚まとめて）...")
            res = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={CONFIG['GEMINI_API_KEY']}",
                json=payload,
                timeout=120
            )
            if res.status_code == 429:
                print(f"  レート制限（試行{attempt}/10）、60秒待機...")
                if attempt < 10:
                    time.sleep(60)
                    continue
                print("  10回失敗、スキップ")
                return None
            if res.status_code == 503:
                print(f"  サーバー一時エラー（試行{attempt}/10）、30秒待機...")
                if attempt < 10:
                    time.sleep(30)
                    continue
                print("  10回失敗、スキップ")
                return None
            res.raise_for_status()
            result = res.json()
            text = result['candidates'][0]['content']['parts'][0]['text'].strip()
            text = re.sub(r'^```json\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  JSONパースエラー: {e}\n  テキスト: {text[:300]}")
            return None
        except Exception as e:
            print(f"  Gemini APIエラー（試行{attempt}/10）: {e}")
            if attempt < 10:
                time.sleep(10)
    print("  10回失敗、スキップ")
    return None




def parse_receipt(data):
    """GeminiのJSON出力(dict)をパースしてデータを返す"""
    if not data:
        return {'date': None, 'date_str': None, 'store_name': None, 'amount': None, 'account_item': '要確認'}

    tax_year = CONFIG['TAX_YEAR']
    date = data.get('date')

    # 月日をMM-DDから取得してTAX_YEARと組み合わせ
    if not date:
        date = None
        date_str = None
    else:
        m = re.search(r'(\d{1,2})[\-/](\d{1,2})', str(date))
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                date = f"{tax_year}{mo:02d}{d:02d}"       # yyyymmdd形式
                date_str = f"{tax_year}-{mo:02d}-{d:02d}" # カレンダー照合用
            else:
                date = None
                date_str = None
        else:
            date = None
            date_str = None

    return {
        'date': date,
        'date_str': date_str,
        'store_name': data.get('store_name'),
        'amount': data.get('amount'),
        'account_item': data.get('account_item', '要確認'),
    }


JST = timezone(timedelta(hours=9))

def match_calendar(calendar_service, date_str, account_item):
    """接待交際費のみ、時刻が近いカレンダー予定を検索"""
    if not date_str or account_item != '接待交際費':
        return None
    try:
        # 時刻付きかどうかで検索幅を変える（JSTとして解釈しRFC3339形式で送信）
        if ' ' in date_str:
            dt = datetime.strptime(date_str.replace('/', '-'), '%Y-%m-%d %H:%M').replace(tzinfo=JST)
            time_min = (dt - timedelta(hours=3)).isoformat()
            time_max = (dt + timedelta(hours=3)).isoformat()
        else:
            dt = datetime.strptime(date_str.replace('/', '-'), '%Y-%m-%d').replace(tzinfo=JST)
            time_min = dt.isoformat()
            time_max = (dt + timedelta(hours=24)).isoformat()

        events = calendar_service.events().list(
            calendarId=CONFIG['CALENDAR_ID'],
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items', [])

        if not events:
            print(f"  カレンダー: {date_str} に予定なし")
            return None

        # 全予定を表示（デバッグ）
        print(f"  カレンダー取得: {[e.get('summary','') for e in events]}")

        # その日の最後の予定を使用
        last_event = events[-1]

        return {
            'title': last_event.get('summary', ''),
            'purpose': last_event.get('summary', ''),  # 説明文は使わずタイトルのみ
        }
    except Exception as e:
        print(f"  カレンダーエラー: {e}")
        return None

# ==================== ファイル名・勘定科目 ====================

def build_filename(parsed, calendar_info, page):
    parts = []
    if parsed['date']:
        parts.append(parsed['date'].replace('/', '').replace('-', ''))
    if parsed['store_name']:
        clean = re.sub(r'[\\/:*?"<>|]', '', parsed['store_name'])[:20]
        parts.append(clean)
    if parsed['amount']:
        parts.append(f"{parsed['amount']}円")
    parts.append(f"p{page}")
    return '_'.join(parts) + '.jpg'

def suggest_account(parsed, calendar_info):
    """parse_receiptで判定済みの勘定科目を返す（飲食は接待交際費固定）"""
    account = parsed.get('account_item', '要確認')
    # カレンダーで飲食と判定されたら接待交際費を確定
    if calendar_info and account in ['要確認', '接待交際費']:
        account = '接待交際費'
    return account

# ==================== スプレッドシート ====================

def append_to_sheet(sheets_service, row, sheet_id, needs_review_col=None):
    """シートに行を追加。needs_review_colが指定された列インデックスのセルを赤背景にする"""
    result = sheets_service.spreadsheets().values().append(
        spreadsheetId=CONFIG['SPREADSHEET_ID'],
        range='経費一覧!A:I',
        valueInputOption='USER_ENTERED',
        body={'values': [row]}
    ).execute()

    if needs_review_col is not None and sheet_id is not None:
        # 追加された行番号を取得
        updated_range = result.get('updates', {}).get('updatedRange', '')
        m = re.search(r'(\d+)$', updated_range)
        if m:
            row_num = int(m.group(1))
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=CONFIG['SPREADSHEET_ID'],
                body={'requests': [{
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_num - 1,
                            'endRowIndex': row_num,
                            'startColumnIndex': needs_review_col,
                            'endColumnIndex': needs_review_col + 1,
                        },
                        'cell': {'userEnteredFormat': {'backgroundColor': {'red': 1, 'green': 0.4, 'blue': 0.4}}},
                        'fields': 'userEnteredFormat.backgroundColor'
                    }
                }]}
            ).execute()

def get_sheet_id(sheets_service):
    """経費一覧シートのsheetIdを取得"""
    meta = sheets_service.spreadsheets().get(spreadsheetId=CONFIG['SPREADSHEET_ID']).execute()
    for s in meta.get('sheets', []):
        if s['properties']['title'] == '経費一覧':
            return s['properties']['sheetId']
    return None

def ensure_sheet_header(sheets_service):
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=CONFIG['SPREADSHEET_ID'],
        range='経費一覧!A1:I1'
    ).execute()
    if not result.get('values'):
        headers = [['日付', '勘定科目', '取引先', '内容', '収入金額', '支払金額', 'ファイル名', 'ファイルリンク', '処理日時']]
        sheets_service.spreadsheets().values().update(
            spreadsheetId=CONFIG['SPREADSHEET_ID'],
            range='経費一覧!A1',
            valueInputOption='USER_ENTERED',
            body={'values': headers}
        ).execute()

# ==================== メイン処理 ====================

def process_receipts():
    print("Google認証中...")
    drive, calendar, sheets = get_google_services()
    ensure_sheet_header(sheets)
    sheet_id = get_sheet_id(sheets)

    print("入力フォルダを確認中...")
    files = drive.files().list(
        q=f"'{CONFIG['INPUT_FOLDER_ID']}' in parents and trashed=false",
        fields='files(id, name, mimeType)'
    ).execute().get('files', [])

    if not files:
        print("処理するファイルがありません")
        return

    for f in files:
        print(f"\n処理中: {f['name']}")

        # ファイルダウンロード
        req = drive.files().get_media(fileId=f['id'])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)

        # 一時ファイルに保存
        safe_name = f['name'].replace('/', '-').replace(' ', '_')
        tmp_path = Path(f'/tmp/{safe_name}')
        tmp_path.write_bytes(buf.read())

        # PDF or 画像で分岐
        if f['mimeType'] == 'application/pdf':
            pages = split_pdf_to_images(tmp_path)
        else:
            pages = [(1, tmp_path.read_bytes())]

        any_success = False

        # 全ページを1リクエストでまとめてOCR
        batch_results = ocr_images_batch(pages)
        if not batch_results:
            print(f"  OCR失敗、スキップ")
            tmp_path.unlink(missing_ok=True)
            continue

        if len(batch_results) != len(pages):
            print(f"  警告: ページ数({len(pages)})とOCR結果数({len(batch_results)})が一致しません")

        for (page_num, img_bytes), gemini_result in zip(pages, batch_results):
            parsed = parse_receipt(gemini_result)
            print(f"  ページ{page_num} 日付:{parsed['date']} 店名:{parsed['store_name']} 金額:{parsed['amount']}")

            cal_info = match_calendar(calendar, parsed.get('date_str'), parsed.get('account_item', ''))
            if parsed.get('account_item') == '接待交際費':
                print(f"  カレンダー照合結果: {cal_info}")

            filename = build_filename(parsed, cal_info, page_num)
            print(f"  ファイル名: {filename}")

            # Drive出力フォルダに保存
            tmp_img = Path(f'/tmp/{filename}')
            tmp_img.write_bytes(img_bytes)
            media = MediaFileUpload(str(tmp_img), mimetype='image/jpeg')
            uploaded = None
            for drive_attempt in range(5):
                try:
                    uploaded = drive.files().create(
                        body={'name': filename, 'parents': [CONFIG['OUTPUT_FOLDER_ID']]},
                        media_body=media,
                        fields='id, webViewLink'
                    ).execute()
                    print(f"  ページ{page_num} Driveアップロード成功: {filename}")
                    break
                except Exception as e:
                    print(f"  Driveアップロード失敗（試行{drive_attempt+1}）: {e}")
                    if drive_attempt < 4:
                        time.sleep(10)
            if not uploaded:
                continue

            # スプレッドシートに記録
            account = suggest_account(parsed, cal_info)
            store = parsed['store_name'] or ''

            # 内容の生成
            content_text = ''
            if account == '旅費交通費' or re.search(r'駐車|パーキング|駐輪', store):
                content_text = '駐車料金'
            elif account == '租税公課':
                content_text = '印紙代'
            elif account == '接待交際費':
                if cal_info and cal_info.get('purpose'):
                    title = cal_info['purpose']
                    if re.search(r'会$|打ち上げ|懇親|研修|会議', title):
                        content_text = title + '_懇親会費'
                    elif re.search(r'様$|先生$|さん$', title):
                        content_text = title
                    else:
                        content_text = random.choice(NEEDS_REVIEW_NAMES)
                else:
                    content_text = random.choice(NEEDS_REVIEW_NAMES)

            row = [
                (parsed['date'] or '')[0:4] + '/' + (parsed['date'] or '00000000')[4:6] + '/' + (parsed['date'] or '00000000')[6:8] if parsed['date'] else '',
                account,
                store,
                content_text,
                '',  # 収入金額（空欄）
                parsed['amount'] or '',
                filename,
                uploaded.get('webViewLink', ''),
                datetime.now().strftime('%Y/%m/%d %H:%M'),
            ]
            # 内容欄が仮入力（要確認）の場合は赤背景
            needs_review = row[3] in NEEDS_REVIEW_NAMES  # D列=インデックス3
            try:
                append_to_sheet(sheets, row, sheet_id, needs_review_col=3 if needs_review else None)
                any_success = True
                print(f"  スプシ記録完了")
            except Exception as e:
                print(f"  スプシ記録失敗: {e}")

        # 1件以上処理できた場合のみ処理済みフォルダに移動
        if any_success:
            drive.files().update(
                fileId=f['id'],
                addParents=CONFIG['DONE_FOLDER_ID'],
                removeParents=CONFIG['INPUT_FOLDER_ID'],
                fields='id'
            ).execute()
            print(f"  → 処理済みフォルダに移動")
        else:
            print(f"  → 全ページ失敗のため入力フォルダに残す")
        tmp_path.unlink(missing_ok=True)

    print("\n全処理完了")

if __name__ == '__main__':
    process_receipts()
