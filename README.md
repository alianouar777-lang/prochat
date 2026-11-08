# ProChat

Desktop chat client for the OpenAI API (streaming, saved chats, model picker, export).

## Get the .exe
1. Create a new GitHub repository and upload all files of this folder (keep the `.github` folder).
2. Open the **Actions** tab, run **Build Windows EXE** (it also runs on every push).
3. When it turns green, open the run and download **ProChat-windows** under *Artifacts*. Unzip it to get `ProChat.exe`.

## First launch
Open **Settings**, paste a *new* API key from platform.openai.com, press **Save**, then **Test**.
The key is stored in Windows Credential Manager. API usage needs credit under Billing
(a ChatGPT Pro subscription does not include API credit).

## Run from source
`python app.py` (Python 3.10+, optional: `pip install keyring`)
Tests: `python -m unittest discover -s tests`
