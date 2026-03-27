# receipt_processor

## 概要
個人事業主の確定申告のレシート管理を自動化するツール。
Google Drive上のスキャン画像をGemini APIで読み取り、日付・金額・店名を抽出してGoogleスプレッドシートに自動記録する。

## 技術構成
- 言語：Python
- OCR：Gemini API（Vision）
- ストレージ：Google Drive API
- 出力：Google Sheets API
- 認証：OAuth2（credentials.json）

## 技術選定方針
**OCRエンジンの選定**
無料枠での運用にこだわり、コスト0で使えるOCRエンジンの組み合わせを検討。
当初Google Cloud Vision APIを試したが、Gemini APIの方が無料枠が広く、プロンプトで抽出項目を柔軟に指定できる点も優れていたため移行。

## 設計方針
- Google Driveの特定フォルダを監視し、未処理画像を順次処理
- 処理済みファイルは別フォルダに移動し二重処理を防止

## 制限事項
- 無料枠のレートリミットにより大量処理には向いていない
- 処理速度は1枚あたり数秒程度（無料枠内での運用を優先したトレードオフ）

## 今後の展望
- ブラウザからカメラ起動→撮影→即時処理できるWebアプリ化
- Google Driveへの依存をなくし、スマホだけで完結する構成へ

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
