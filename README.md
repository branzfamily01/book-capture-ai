# Book Capture AI

Windows向けのローカル電子書籍キャプチャ支援アプリです。

通常表示できる電子書籍画面を、

**自動ページ送り → キャプチャ → Smart Page Guard → Kindle UIトリミング → 見開き自動分割 → PDF/OCR化**

します。

> DRM解除、暗号化解除、キャプチャ防止機能の回避は行いません。  
> 自分が権利を持つ、または私的利用等の許される範囲で使用してください。

## v0.5 の主な機能

- Windows上の対象ウィンドウ選択
- マウスドラッグによるキャプチャ範囲指定
- ← / → / Space / PageDown による自動ページ送り
- Windowsネイティブのキー送信（PyAutoGUI fail-safeに依存しない）
- Smart Page Guard
- 元の見開きPNG保存
- **KindleクリーンPDF**
  - 上部ヘッダー除去: 初期値 8%
  - 下部フッター除去: 初期値 6%
  - 上下それぞれON/OFF可能
  - 0.5%刻みで調整可能
  - 元画像は切らず `images/` に保持
- **見開き中央自動分割**
  - デフォルト: 右 → 左（日本語書籍向け）
  - 左 → 右にも切替可能
- 分割後・トリミング後の1ページ画像を `images-split/` に保存
- 1ページ表示で分割OFFの場合はトリミング後画像を `images-clean/` に保存
- クリーン画像から画像PDF生成
- 内蔵Tesseract OCRで検索可能PDF / OCR TXT生成
- 設定値の保持

## 推奨設定（Kindle for Web・見開き）

- 見開き自動分割: ON
- ページ順: 右 → 左（日本語本）
- 上部ヘッダー除去: ON / 8%
- 下部フッター除去: ON / 6%
- OCR言語: `jpn+eng`

添付テストで確認されたKindle UIの「上部書名表示」と、下部の「読書速度を学習中… / 位置表示」を、PDF/OCRの前に切り落とすことを狙った初期値です。書籍・表示倍率・ブラウザ幅によって必要量が違う場合は%を調整してください。

## 見開きモードの出力

```text
本の名前-日時/
├── 本の名前.pdf
├── 本の名前-searchable.pdf
├── 本の名前.txt
├── images/              # 元の見開きスクリーンショット（無加工）
│   ├── page-0001.png
│   └── ...
└── images-split/        # 上下トリミング＋1ページ分割後
    ├── page-0001.png
    ├── page-0002.png
    └── ...
```

## 1ページ表示の出力

見開き分割OFFで上下トリミングをONにすると `images-clean/` を作り、PDF/OCRはその画像を使います。

## 使い方

1. WindowsでKindle for Web等を開き、本を表示します。
2. `Book Capture AI` を起動します。
3. 「ウィンドウ更新」→ 対象を選択します。
4. 「画面範囲を選択」で、見開き全体または1ページ全体を囲みます。
5. Kindleのページ送り方向を指定します。
6. 見開きなら「見開きを中央から2ページに自動分割する」をONにします。
7. 日本語書籍ならページ順を「右 → 左」にします。
8. 「上部ヘッダーを除去」「下部フッターを除去」をONにします。まず 8% / 6% を推奨します。
9. Kindleを全画面へ戻し、マウスを動かさず `F7` を押します。3秒後、上下の操作バーが消えてから開始します。
10. 全画面のまま `F8` で一時停止／再開、`F9` を2回で終了してPDF作成できます。
11. 終了後、トリミング → 分割 → PDF → OCRまで自動処理します。

## OCRについて

Windows配布版には **Tesseract OCR と日本語・英語モデルを同梱**しています。別途インストール不要です。

- `jpn+eng`: 日本語横書き＋英語（標準）
- `jpn_vert+eng`: 日本語縦書き＋英語

OCRはローカルPC内で処理します。

## 開発環境から起動

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app_v5.py
```

## EXEを作る

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name "BookCaptureAI" ^
  --collect-all pygetwindow ^
  app_v5.py
```

## GitHub Actions

`.github/workflows/build-windows.yml` でWindows runner上の自動ビルド・テストを行います。

Artifact名:

```text
BookCaptureAI-Windows-v0.5-Clean-Split-OCR
```

テストには、見開き順序、奇数幅、上下8%/6%トリミング、1ページモード、通常PDF、内蔵OCR、検索可能PDF、完成EXE自己診断を含みます。

## 既知の制約

- トリミング位置は現在「画像高さに対する割合」です。Kindle UIや表示倍率が違えば調整が必要です。
- 見開き分割は選択範囲の中央で分割します。
- 表紙や章扉など片側だけにページがある画面では、不要な半分が生成されることがあります。
- OCR品質は元画面解像度・文字サイズ・Tesseractモデルに依存します。
- キャプチャ禁止・保護機能は回避しません。

## 次候補

- ヘッダー/フッター境界の自動検出
- 中央余白の自動検出による分割位置補正
- 空白側ページの自動スキップ
- ページ番号OCRによる欠落検知
- 100%/最終ページ判定
- PDF圧縮
