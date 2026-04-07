# Soapbox Speed Camera

Raspberry Pi で動かす簡易速度計測システムです。  
ブラウザ UI から ROI、Perspective、Scale、Line Crossing、検知パラメータを調整できます。

## できること

- USB / CSI / RTSP カメラ入力
- ブラウザからライブ確認と設定編集
- ROI 設定
- 4点 Perspective 設定
- 2点 Scale 設定
- `Tracking` と `Line Crossing` の 2 モード
- 最新検知ログ表示
- CSV ログ出力

## 起動

```bash
python web_config.py
```

ブラウザで `http://<IPアドレス>:5000` を開きます。

## 主なモード

### Tracking

通常の追跡モードです。動いている物体を追跡し、フレーム間移動量から速度を出します。  
Scale 未設定の間は `px/s`、Scale 設定後は `km/h` 表示になります。

補足:

- `min_speed_kmh` と `max_speed_kmh` はどちらも `km/h` です
- `min_speed_kmh` より小さい速度は表示やログから外れます

### Line Crossing

`Line A` と `Line B` を通過した時間差から速度を確定するモードです。  
大量の瞬間速度表示を減らしたいときに向いています。

必要な設定:

- `Line A`
- `Line B`
- `distance_m`

補足:

- 同じフレーム間で 2 本をまたいだ場合も、交差位置から通過時刻を補間して計算します
- 連続表示がうるさい場合は `repeat behavior` と `repeat cooldown seconds` を使います

## デバッグモード

`debug mode` を ON にすると、以下のように検知を甘めにします。

- `min_contour_area` を小さめに補正
- `track_max_distance` を少し広げる
- `threshold_value` を少し下げる
- 画面下部に簡易デバッグ情報を表示

小さいミニカーや仮の物体でテストするときに便利です。  
本番に近づけるときは OFF を推奨します。

`2値化プレビュー` は debug mode とは別の ON/OFF です。  
ライブ左上の小窓に二値化マスクを出すかどうかを切り替えます。

## Perspective の考え方

Perspective は「検知や計測のための補正座標系」として使います。  
ライブ表示は広めの元映像を維持しつつ、内部では補正後座標で ROI や Line Crossing を評価します。

UI 上の 4 点順序は次の通りです。

1. 左上
2. 右上
3. 右下
4. 左下

## Downscale について

`downscale_factor` を下げると負荷が軽くなります。  
その代わり細かい物体は拾いにくくなります。

現在は `downscale_factor` 変更時に、次の座標を相対的に補正します。

- ROI
- Perspective 4点
- Scale 2点
- Line A / Line B

Scale の `pixel_distance` と `ppm` も合わせて再計算します。

## よく触る設定

- `min_contour_area`: 小さなノイズを拾いにくくする
- `max_contour_area`: 大きすぎる誤検知を除外する
- `threshold_value`: 背景差分後の白黒化の強さ
- `min_speed_kmh`: 遅すぎる検知を表示しないための下限
- `background_history`: 背景モデルの安定度
- `track_max_distance`: 同一物体とみなす距離
- `repeat behavior`: 再検知時の扱い
- `repeat cooldown seconds`: 再検知を抑える秒数

## ファイル

- `web_config.py`: Flask アプリと API
- `speed_estimator_core.py`: 検知と速度計算
- `camera_manager.py`: カメラ入力管理
- `config_manager.py`: 設定管理
- `config.json`: 保存設定
- `templates/`, `static/`: ブラウザ UI

## メモ

- `Perspective Preview` は静止画です
- `/stream` は MJPEG 配信です
- 重いときは `downscale_factor` を下げる、`Blur` / `Morphology` を切る、`debug mode` を OFF にするのが効きやすいです
