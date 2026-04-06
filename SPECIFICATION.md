# プロジェクト仕様書

## 1. プロジェクト概要
Raspberry Pi + カメラを用いたソープボックスダービー車両速度推定システム。  
コマンドラインで速度推定を実行し、ブラウザから各種設定を行う。

対象：青い床面のレースコースを2階から斜め撮影  
目標精度：テスト段階のため ±10%以内（後ほど調整）

## 2. 機能要件

- カメラ抽象化（USB / CSI）
- レンズ歪み補正（OpenCV calibrateCamera）
- 4点Perspective補正（homography）
- 多角形ROI設定
- 2点クリックによる実スケール設定（ppm計算）
- 背景差分（MOG2）＋輪郭検出＋簡易追跡による速度計算
- MJPEGストリーム（速度オーバーレイ表示）
- CSVログ出力（タイムスタンプ、速度、IDなど）

## 3. 技術スタック

- Python 3.11+
- OpenCV（apt提供のpython3-opencvを優先）
- picamera2（CSIカメラ用）
- Flask（設定UI + MJPEGサーバー）
- NumPy / SciPy

## 4. 設定ファイル（config.json）

（前回お渡ししたJSON構造をここに貼り付け可能。省略時は必要に応じて追記）

詳細は [CONFIGURATION.md](CONFIGURATION.md) を参照。