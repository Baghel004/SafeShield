# Going live — a complete walkthrough

This is the hand-holding version, written for someone who has not deployed to
AWS before. It assumes Windows 11 (which is what this project was built on) and
takes you from nothing to a live, public URL, then back down to zero cost.

`ops/RUNBOOK.md` is the reference — the *why* behind each piece. This is the
*how*, in order, with the exact commands.

---

## Before you start — read this once

**This costs real money.** The backend runs on AWS EKS, which is **not** free
tier. While it is up it costs about **$0.28/hour** (~$7/day, ~$210/month). A
short test session is about **$1–2**. The whole design is built so you bring it
up, use it, and **tear it down the same day**.

**The single most important habit:** when you finish, run `down.sh` and wait for
it to say everything is gone. Forgetting is how you get a surprise bill.

**You will need:**
- A credit/debit card (AWS requires one even for a demo account).
- About **30 minutes** for the first-ever run (AWS takes ~25 min to build the
  cluster), and ~15 minutes to tear down.
- The OpenAI API key you already use for this project.

**Where you type commands:** open **Git Bash** (not PowerShell or CMD). The
deploy scripts are bash scripts. Right-click inside the `SafeShield` project
folder in File Explorer and choose **"Git Bash Here"**, or open Git Bash and
run `cd /c/Users/baghe/OneDrive/Desktop/SafeShield`.

---

## Step 1 — Install the tools

The deploy script checks for eight tools and refuses to run without them. You
already have some (Docker, Git, Node, kubectl came with Docker Desktop). Install
the rest.

Open **PowerShell** (just for this step — `winget` is a Windows installer) and
run:

```powershell
winget install Amazon.AWSCLI
winget install Hashicorp.Terraform
winget install Helm.Helm
winget install jqlang.jq
```

Then **close and reopen** all terminals so they pick up the new tools.

Now back in **Git Bash**, check everything is present:

```bash
aws --version
terraform -version
kubectl version --client
helm version
docker --version
git --version
jq --version
node --version && npm --version
```

Each should print a version. If one says "command not found", re-install it. If
`winget` cannot find a package, search the tool's name + "windows download" and
use the official installer.

**Docker Desktop must be running** — look for the whale icon in your system
tray. The script builds a container image, which needs the Docker engine up.

---

## Step 2 — Create an AWS account and get credentials

### 2a. The account (skip if you already have one)

1. Go to <https://aws.amazon.com/> and click **Create an AWS Account**.
2. Enter an email, account name, and a password.
3. Enter your address and a **credit/debit card** (required; you are not charged
   to create the account).
4. Verify your phone number (they send a code).
5. Choose the **Basic (free)** support plan.

This takes 10–15 minutes and the account is usually usable within a few minutes.

### 2b. Create a login for the CLI

You should not use your root account for day-to-day work. Create an admin user:

1. Sign in to the **AWS Console** at <https://console.aws.amazon.com/>.
2. In the search bar at the top, type **IAM** and open it.
3. Left menu → **Users** → **Create user**.
4. Name it `safeshield-admin`. Click **Next**.
5. Choose **Attach policies directly**, tick **AdministratorAccess**, click
   **Next**, then **Create user**.

### 2c. Get an access key

1. Click the `safeshield-admin` user you just made.
2. Tab **Security credentials** → scroll to **Access keys** → **Create access
   key**.
3. Choose **Command Line Interface (CLI)**, tick the confirmation, **Next**,
   **Create access key**.
4. You now see an **Access key ID** and a **Secret access key**. **Copy both
   now** — the secret is shown only once. (If you lose it, just delete the key
   and make a new one.)

### 2d. Tell your computer about the credentials

In **Git Bash**:

```bash
aws configure
```

It asks four things:
- **AWS Access Key ID** — paste the key ID from 2c.
- **AWS Secret Access Key** — paste the secret from 2c.
- **Default region name** — type `ap-south-1` (Mumbai; the project defaults to
  it). Use a closer region if you prefer, but then also set it in Step 3.
- **Default output format** — type `json`.

Verify it works:

```bash
aws sts get-caller-identity
```

You should see your account number and the `safeshield-admin` user ARN. If you
see an error, the keys are wrong — re-run `aws configure`.

---

## Step 3 — Create your config file

The deploy reads one settings file. Copy the example and edit it:

```bash
cd /c/Users/baghe/OneDrive/Desktop/SafeShield
cp deploy/terraform/ondemand.tfvars.example deploy/terraform/ondemand.tfvars
```

Open `deploy/terraform/ondemand.tfvars` in a text editor (VS Code, Notepad) and
set two things:

```hcl
deletion_protection = false            # leave this false, or you cannot tear down
billing_alarm_email = "you@example.com"  # <- your real email
```

Leave everything else as-is. **If you chose a region other than `ap-south-1`**
in Step 2d, also add a line `region = "your-region"` here, and set it in Git
Bash for this session: `export AWS_REGION=your-region`.

Save the file. (It is gitignored, so your email never gets committed.)

---

## Step 4 — Bring it live

Make sure **Docker Desktop is running**, then in Git Bash from the project
folder:

```bash
deploy/scripts/up.sh
```

### What happens, and the one interruption to expect

The script prints its progress. The first phase — building the cluster and
database — takes **~20 minutes**. Let it run.

**On your very first run only, it will stop** with a message like:

```
The application secret is not populated yet. Set it once, then re-run this script:

  aws secretsmanager put-secret-value --region ap-south-1 --secret-id safeshield-prod/app \
    --secret-string '{"JWT_SECRET":"...","OPENAI_API_KEY":"sk-..."}'
```

This is expected and safe. It is telling you to store your secret keys in AWS
(the tool never puts your keys in a file). Do this:

1. Copy the command it printed.
2. Replace `sk-...` with your real OpenAI API key. (The `JWT_SECRET` part
   generates itself — leave that as printed.)
3. Paste and run it in Git Bash. It prints the secret's ARN — that means it
   worked.
4. Run `deploy/scripts/up.sh` **again**. It skips the 20 minutes already done
   and continues from where it stopped. This interruption only ever happens
   once; the keys persist.

### When it finishes

You will see:

```
==> Up.
  Frontend: https://xxxxxxxx.cloudfront.net
  API:      https://k8s-xxxx.ap-south-1.elb.amazonaws.com/api/health
```

**Also confirm the billing alarm:** AWS emails the address from Step 3 asking you
to confirm an SNS subscription. Click the link in that email once, so the
"you're spending money" alarm can actually reach you.

---

## Step 5 — Check it works

1. Open the **Frontend** URL in your browser. (Give it 2–3 minutes after the
   script finishes — the load balancer takes a moment to start passing traffic.
   If you see "backend offline", wait and refresh.)
2. Click **Try the demo**.
3. Ask a question, e.g. *"Is cataract treatment excluded from cover?"*
4. You should see an answer stream in with numbered source citations.

That is the whole app, live on the internet. The Frontend URL is the one to put
on a CV or share — it stays up even after you tear the backend down (it will
show the "backend offline" banner when the backend is down, which is fine).

---

## Step 6 — Tear it down (do not skip this)

When you are done for the session:

```bash
deploy/scripts/down.sh
```

It removes the release, destroys the infrastructure, and then **checks AWS
directly** that nothing is left running — printing a line for each resource
type. It ends with:

```
==> Down. Nothing hourly is left.
```

If instead it says **"Some resources survived"** and lists items marked
`STILL RUNNING`, those are billing — do not walk away. Re-run `down.sh`; if it
still reports something, open the AWS Console, search for that service, and
delete the item by hand. (This is rare, but it is exactly the situation the
check exists to catch.)

The frontend, the container images, and your saved secret are left in place on
purpose — together they cost about **$1–3/month** and are what keep your public
link alive. To remove those too (only if you are done with the project
entirely), destroy the frontend module:

```bash
terraform -chdir=deploy/terraform-frontend destroy
```

---

## If something goes wrong

- **A script errors with `'\r'` or "bad interpreter":** the file has Windows
  line endings. Fix it once with:
  `sed -i 's/\r$//' deploy/scripts/*.sh` and run again.
- **"missing required tool":** re-do Step 1 for that tool and reopen Git Bash.
- **"AWS credentials are not configured":** your session expired or keys are
  wrong — re-run `aws configure` (Step 2d).
- **A pod will not start, or the URL never loads:** the three things a local
  test could not check are IRSA (the app's permission to reach S3), the S3
  bucket, and the load balancer getting a public address. Run
  `kubectl -n safeshield get pods` and
  `kubectl -n safeshield describe pod <name>` and share the output — that is
  where the answer will be.
- **You are unsure whether anything is still running/billing:** run
  `deploy/scripts/down.sh` again. It is safe to run repeatedly and tells you the
  truth about what exists.

---

## The whole thing, condensed

```bash
# one-time setup
winget install Amazon.AWSCLI Hashicorp.Terraform Helm.Helm jqlang.jq   # (in PowerShell)
aws configure                                                          # keys + ap-south-1 + json
cp deploy/terraform/ondemand.tfvars.example deploy/terraform/ondemand.tfvars
#   ...edit that file: billing_alarm_email

# every session
deploy/scripts/up.sh      # first time: stops once to set the secret, then re-run
#   ...use the printed Frontend URL...
deploy/scripts/down.sh    # ALWAYS, when finished
```
