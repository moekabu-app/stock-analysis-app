# クラウド公開の準備

このフォルダには、クラウド公開に必要なファイルを入れています。

## GitHubへ置くファイル

- `stock_helper.py`
- `requirements.txt`
- `.gitignore`
- `secrets.example.toml`（見本なので公開可）

`secrets.toml` は作らないか、作ってもGitHubへ送らないでください。

## Streamlit Community Cloudの秘密設定

アプリ公開時に、`secrets.example.toml` を見本にして以下を入力します。

- EDINET DBのAPIキー
- GoogleログインのクライアントIDとクライアントシークレット
- Cookie用のランダム文字列
- 利用を許可する最大5人分のGoogleメールアドレス

## 利用者を増減するとき

CloudのSecretsにある `allowed_emails` へメールアドレスを追加または削除します。
Google Cloud側のテスト利用者にも同じメールアドレスを追加してください。
