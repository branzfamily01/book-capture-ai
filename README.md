# Book Capture AI

Windows向けのローカル電子書籍キャプチャ支援アプリです。

通常表示できる電子書籍画面を、

**自動ページ送り → キャプチャ → 重複/最終画面検出 → PDF化 → 任意でOCR**

します。

> DRM解除、暗号化解除、キャプチャ防止機能の回避は行いません。  
> 自分が権利を持つ、または私的利用等の許される範囲で使用してください。

## MVP機能

- Windows上の対象ウィンドウ選択
- マウスドラッグによるキャプチャ範囲指定
- ← / → / Space / PageDown による自動ページ送り
- Smart Page Guard
  - 前ページとの画像差分判定
  - 同一画面連続時の自動停止
  - ページ表示安定待ち
- 最大ページ数
- 一時停止 / 再開 / 手動終了
- PNG保存
- 画像PDF生成
- 内蔵OCRで検索可能PDF生成
- 内蔵OCRでOCRテキスト生成
- 設定値の保持

## 使い方

1. Windowsで電子書籍アプリまたはブラウザを開き、本を表示します。
2. `Book Capture AI` を起動します。
3. 「ウィンドウ更新」→ 対象を選択します。
4. 「画面範囲を選択」で本文だけを囲みます。
5. ページ送り方向を指定します。
6. 「キャプチャ開始」を押します。
7. 同じ画面が規定回数続くか、最大ページ数に達すると終了します。
8. 終了後、自動でPDFを生成します。

## OCRについて

Windows配布版には **Tesseract OCR と日本語・英語モデルを同梱**しています。
Tesseractを別途インストールする必要はありません。

- `jpn+eng`: 日本語横書き＋英語（標準）
- `jpn_vert+eng`: 日本語縦書き＋英語（必要な場合に指定）

OCRはローカルPC内で処理し、OCRのための外部サーバー送信は行いません。

## 開発環境から起動

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## EXEを作る

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name "BookCaptureAI" ^
  --collect-all pygetwindow ^
  app.py
```

生成物:

```text
dist/BookCaptureAI/
```

## GitHub Actions

`.github/workflows/build-windows.yml` を含めています。

GitHubにpushするとWindows runner上でビルドし、ActionsのArtifactsから
`BookCaptureAI-Windows-v0.3-OCR-Bundled` を取得できます。

## 既知の制約

- Windows向けMVPです。
- 電子書籍側のアニメーションが大きい場合は「表示安定待ち」を長くしてください。
- ページ内アニメーションが常時動くコンテンツでは画像差分の閾値調整が必要です。
- OCR品質はTesseractと言語データ、元画面解像度に依存します。
- キャプチャを禁止しているコンテンツや保護機能は回避しません。

## 次候補

- 見開き自動分割
- ページ番号OCRによる欠落検知
- 100%/最終ページ判定
- 自動トリミング
- PDF圧縮
- AI投入用の自動分割
