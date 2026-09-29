# The way in

A box whose runner refuses *before* it fetches can be reached by no push, no
request and no release: the thing that would apply any of them is the thing that
is refusing. Measured on this box — 19 commits behind with
`M app/routes/admin_sekolah.py`, three ssh keys and every stored password refused,
and the provider's console behind a captcha — the only remaining channel was a
human at a noVNC terminal that cannot paste.

So the box installs its own way in, out of the repository it already trusts. Every
file here ending in `.pub` is appended by the deploy runner to the deploy
account's `authorized_keys`, on every tick:

* **idempotently**, by the key's type and blob rather than by the line, so a
  second tick appends nothing and the same key with a different trailing comment
  is not a second key;
* **without ever rewriting the file**, so a key somebody else put there is not
  this runner's to remove;
* **only plain public keys** — a line carrying `authorized_keys` options is
  refused and reported, because a file sshd parses is not a place for this script
  to author options into;
* **never printing key material**, so a file that should not have been named
  `.pub` is not echoed into the journal by the install that refused it.

## What may live here

Public halves only. A private key in this directory is a private key in a git
history, and the point of the arrangement is that the half which opens the door
stays on the operator's machine. It is not a secret that this key exists: it is a
`ssh-ed25519` line, and the same key is in the deploying developer's
`~/.ssh/scangrade_deploy.pub` with the private half beside it. Removing a granted
key is therefore one edit on the box — delete its line from
`/root/.ssh/authorized_keys` — and never a code change.

`tests/unit/test_deploy_key.py` holds every property above, and the shape of this
directory: exactly one `.pub` per key, no options in them, and nothing that looks
like a private half anywhere beside them.
