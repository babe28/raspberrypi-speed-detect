3. INSTALLATION.md
# インストール手順

## Raspberry Pi OS trixie 向け

1. システム更新
```bash
sudo apt update && sudo apt full-upgrade -y

依存パッケージインストール

Bashsudo apt install -y python3-full python3-venv python3-opencv python3-picamera2 \
    libopenblas-dev libjpeg-dev libtiff-dev libpng-dev \
    libavcodec-dev libavformat-dev libswscale-dev libgtk-3-dev \
    build-essential cmake

仮想環境作成

Bashcd ~/soapbox-speed-camera
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask numpy scipy

初回設定

Bashpython web_config.py
ブラウザで設定を行い、config.json を保存してください。
仮想環境の有効化（毎回必要）
Bashsource venv/bin/activate
注意
Pi Zero 2 Wではメモリ・CPUが限られるため、カメラ解像度やダウンスケール係数を調整してください。
text### 4. CONFIGURATION.md（設定方法の詳細）

```markdown
# 設定方法

ブラウザ（http://<IP>:5000）から以下の設定を行います：

1. **カメラ設定**  
   - カメラ種別（usb / csi）
   - 解像度・FPS

2. **キャリブレーション**  
   - レンズ歪み補正（chessboardを提示して実行）

3. **Perspective補正**  
   - レースコースの長方形領域を4点クリックで指定

4. **ROI設定**  
   - 多角形を描画（クリックで頂点追加 → 閉じる）

5. **スケール設定**  
   - コース上に既知長さの物体を置き、2点クリック → 実測距離（m）を入力

設定は即時 `config.json` に保存され、メイン処理に反映されます。