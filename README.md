# Soapbox Speed Camera

Raspberry Pi を用いたソープボックスダービー車両速度推定システムです。

2階からの斜め撮影環境に対応し、ブラウザから設定（多角形ROI、4点Perspective補正、レンズ歪み補正、スケール校正）が可能です。  
メイン処理はコマンドラインで動作し、MJPEGストリームでリアルタイムに速度オーバーレイを表示します。

## 特徴

- USBカメラ / CSIカメラ（Pi Camera Module 3など）両対応
- 多角形ROI設定
- 4点指定によるPerspective補正（レースコース長方形領域を真上視に変換）
- レンズ歪み補正（chessboard校正対応）
- 2点クリックによるスケール校正（pixels-per-meter）
- 背景差分＋輪郭追跡による軽量速度推定（Pi Zero 2 W対応）
- Flaskによるブラウザ設定画面＋MJPEGライブストリーム
- CSVログ出力（速度・タイムスタンプ）

## 動作環境

- **Raspberry Pi**: Zero 2 W（デバッグ用）／Pi 4 / Pi 5 推奨
- **OS**: Raspberry Pi OS trixie（64-bit）
- **カメラ**: USBカメラ（現行）または CSIカメラ
- **解像度**: 1920×1080@30fps キャプチャ（処理時はダウンスケール推奨）

## クイックスタート

### 1. リポジトリのクローン
```bash
cd ~
git clone https://github.com/yourusername/soapbox-speed-camera.git
cd soapbox-speed-camera
2. システムパッケージのインストール
Bashsudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-full python3-venv python3-opencv python3-picamera2 \
    libopenblas-dev libjpeg-dev libtiff-dev libpng-dev \
    libavcodec-dev libavformat-dev libswscale-dev libgtk-3-dev \
    build-essential cmake
3. 仮想環境の作成とパッケージインストール
Bashpython3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install flask numpy scipy
4. プロジェクトの実行
設定サーバー（ブラウザで設定）
Bashsource venv/bin/activate
python web_config.py
→ ブラウザで http://<RaspberryPiのIP>:5000 にアクセス
メイン速度推定処理
Bashsource venv/bin/activate
python speed_estimator.py --stream
ファイル構成

speed_estimator.py — メインCLI処理
camera_manager.py — カメラ抽象化（USB / CSI）
speed_estimator_core.py — 速度推定ロジック
config_manager.py — 設定ファイル管理
web_config.py — Flask設定・MJPEGサーバー
config.json — ROI、補正行列、スケールなどの設定
static/ — HTML/JS/CSS（ブラウザUI）
logs/ — CSVログ出力先
```

## 詳細仕様
詳細は以下を参照してください：

- SPECIFICATION.md
- INSTALLATION.md
- CONFIGURATION.md
- DEVELOPMENT.md

## 注意事項

Pi Zero 2 Wでは処理負荷を抑えるため、downscale_factor を0.4〜0.6程度に調整してください。
本番運用時はPi 5へのアップグレードを推奨します。
ネットワークは同一LAN内完結（インターネット接続不要）

## ライセンス
MIT License