# Book Capture AI

Windows向けのローカル電子書籍キャプチャ支援アプリです。

通常表示できる電子書籍画面を、

**自動ページ送り → キャプチャ → Smart Page Guard → 見開き自動分割 → PDF/OCR化**

します。

> DRM解除、暗号化解除、キャプチャ防止機能の回避は行いません。  
> 自分が権利を持つ、または私的利用等の許される範囲で使用してください。

## v0.4 の主な機能

- Windows上の対象ウィンドウ選択
- マウスドラッグによるキャプチャ範囲指定
- ← / → / Space / PageDown による自動ページ送り
- Windowsネイティブのキー送信（PyAutoGUI fail-safeに依存しない）
- Smart Page Guard
  - 前ページとの画像差分判定
  - 同一画面連続時の自動停止
  - ページ表示安定待ち
- 最大ページ数
- 一時停止 / 再開 / 手動終了
- 元の見開きPNG保存
- **見開き中央自動分割**
  - デフォルト: 右 → 左（日本語書籍向け）
  - 左 → 右にも切替可能
  - 奇数ピクセル幅にも対応
- 分割後の1ページ画像を `images-split/` に保存
- 分割後画像から画像PDF生成
- 内蔵OCRで検索可能PDF生成
- 内蔵OCRでOCRテキスト生成
- 設定値の保持

## 見開きモードの出力

```text
本の名前-日時/
├── 本の名前.pdf
├── 本の名前-searchable.pdf
├── 本の名前.txt
├── images/              # 元の見開きスクリーンショット
│   ├── page-0001.png
│   └── ...
└── images-split/        # 1ページずつに分割した画像
    ├── page-0001.png
    ├── page-0002.png
    └── ...
```

見開き自動分割をOFFにすれば、従来どおり1回のキャプチャをPDFの1ページとして使えます。

## 使い方

1. WindowsでKindle for Web等を開き、本を表示します。
2. `Book Capture AI` を起動します。
3. 「ウィンドウ更新」→ 対象を選択します。
4. 「画面範囲を選択」で、見開き全体または1ページ全体を囲みます。
5. Kindleのページ送り方向を指定します。
6. 見開きなら「見開きを中央から2ページに自動分割する」をONにします。
7. 日本語書籍ならページ順を「右 → 左」にします。
8. 「キャプチャ開始」を押します。
9. 終了後、必要に応じて分割 → PDF → OCRまで自動処理します。

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
python app_v4.py
```

## EXEを作る

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name "BookCaptureAI" ^
  --collect-all pygetwindow ^
  app_v4.py
```

## GitHub Actions

`.github/workflows/build-windows.yml` でWindows runner上の自動ビルド・テストを行います。

Artifact名:

```text
BookCaptureAI-Windows-v0.4-Split-OCR
```

自動テストには、見開き右→左/左→右、奇数幅分割、通常PDF、内蔵OCR、検索可能PDF、完成EXE自己診断を含みます。

## 既知の制約

- 見開き分割は現在「選択範囲の中央」で分割します。
- 表紙や章扉など片側だけにページがある特殊画面では、不要な半分が生成されることがあります。
- 電子書籍側のアニメーションが大きい場合は「表示安定待ち」を長くしてください。
- OCR品質は元画面解像度・文字サイズ・Tesseractモデルに依存します。
- キャプチャを禁止しているコンテンツや保護機能は回避しません。

## 次候補

- 中央余白の自動検出による分割位置補正
- 空白側ページの自動スキップ
- ページ番号OCRによる欠落検知
- 100%/最終ページ判定
- 自動トリミング
- PDF圧縮
