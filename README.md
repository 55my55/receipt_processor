# receipt_processor

レシート・領収書を自動処理して経費一覧スプレッドシートに記録するツール。

## 処理の流れ

1. **Google Drive** の入力フォルダからPDF・画像を取得
2. **Gemini Vision API** でOCR（日付・店名・金額・勘定科目を抽出）
3. **Google Calendar** を照合して接待交際費の内容を補完
4. 処理済み画像を **Google Drive** の出力フォルダに保存
5. **Google スプレッドシート** の「経費一覧」シートに追記

## 必要なもの

- Python 3.10+
- Google Cloud プロジェクト（Drive / Calendar / Sheets API を有効化）
- Gemini API キー

## セットアップ

```bash
pip install pymupdf pillow requests google-auth google-auth-oauthlib google-api-python-client
```

`credentials.json`（Google OAuth クライアント）をホームディレクトリに配置し、環境変数を設定：

```bash
export GEMINI_API_KEY=your_api_key_here
```

## 使い方

```bash
python receipt_processor.py
```

初回実行時にブラウザが開き、Googleアカウントの認証を求められます。
認証後は `token.json` が自動生成され、以降は自動ログインします。

## 設定

`receipt_processor.py` 内の `CONFIG` 辞書で以下を設定してください：

| キー | 説明 |
|------|------|
| `INPUT_FOLDER_ID` | 処理対象PDFを置くGoogle DriveフォルダID |
| `OUTPUT_FOLDER_ID` | 処理済み画像の保存先フォルダID |
| `DONE_FOLDER_ID` | 元PDFの移動先フォルダID |
| `SPREADSHEET_ID` | 記録先スプレッドシートID |
| `CALENDAR_ID` | 照合対象のGoogleカレンダーID |
| `TAX_YEAR` | 確定申告の対象年度 |

## 注意

- `credentials.json` と `token.json` は `.gitignore` で除外済みです。絶対にコミットしないでください。
- `GEMINI_API_KEY` は環境変数で渡してください。ソースコードに直書きしないでください。
