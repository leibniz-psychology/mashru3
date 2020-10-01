mashru3
=======

mashru3 (مشروع) provides the command ``workspace``, which sets up isolated
environments driven by ``guix environment``. It is essentially just glue code
on top of guix_ and conductor_.

A common workflow would look like this:

.. code:: console

	# Create new workspace and enter it
	cd `workspace create my new fancy workspace`
	# Add some code
	$EDITOR hello.R
	# Run rstudio inside container (needs psychnotebook-app-rstudio)
	workspace run rstudio

It’s also possible to share a workspace with users on the same machine by running

.. code:: console

	workspace share janeuser

which would grant the *group* ``janeuser`` read access to the folder via ACL’s.
``-w`` allows writing as well, but be careful with that.

Then ``janeuser`` can copy the entire workspace with exactly the same Guix
environment:

.. code:: console

	workspace copy ~joeuser/my_new_fancy_workspace janescopy

Internals
---------

Workspaces are simply directories with a certain structure. They contain

1) their own copy of Guix
2) a Guix channel description (``~/.config/guix/channels.scm``), pinning it to
   a fixed commit and a manifest with installed packages
   (``~/.config/guix/manifest.scm``)
3) metadata like name, description, …

Applications drop a freedesktop-compliant `.desktop`_ file into one of the
``XDG_DATA_DIRS``, usually ``~/.guix-profile/share/applications``, but
``~/.local/share/applications`` for user-supplied applications is supported as
well. If the program has a web interface it must be proxied through conductor_,
which is indicated by the interface ``org.leibniz-psychology.org.conductor.vX``
specified in the ``.desktop`` file. Version 1 requires commands to accept the
argument ``-s <socketpath>`` and listen on ``socketpath``.

``workspace run`` spawns a Guix container (i.e. Linux namespace) and mounts the
workspace directory as homedir of the fake user ``joeuser``. Note that the UID
1000 must exist on the host system.

.. _.desktop: https://specifications.freedesktop.org/desktop-entry-spec/latest/
.. _conductor: https://github.com/leibniz-psychology/conductor
.. _guix: https://guix.gnu.org/

