# 開発ガイド

VS Code + GitHub Copilot (Codex) を使用して開発を進めてください。

## 推奨開発フロー

1. `camera_manager.py` から実装開始（カメラ抽象化）
2. `config_manager.py`（設定読み書き）
3. `speed_estimator_core.py`（速度推定ロジック）
4. `web_config.py`（Flaskサーバー）
5. `speed_estimator.py`（メインエントリポイント）

各ファイル作成時に以下のプロンプト例を使用すると効率的です：

**例（camera_manager.py）**  
「Raspberry Pi Zero 2 W向けに、USBカメラ（cv2.VideoCapture）とCSIカメラ（picamera2）を抽象化したCameraManagerクラスを作成してください。config.jsonから設定を読み、read()メソッドでフレームを返し、ダウンスケールオプションをサポート。仮想環境対応。」

## 注意点

- OpenCVは `apt` でインストールした `python3-opencv` を使用（venv内でimport可能）
- 重い処理は避け、古典的CV（背景差分＋輪郭）を優先
- Pi Zero 2 W性能を考慮し、常にフレームレートをモニタリング