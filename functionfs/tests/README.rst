Running tests
=============

On device
---------

.. code:: sh

  sudo python -m functionfs.tests.device --username $SOME_USER

On host
-------

In a shell with permissions to open the device:

.. code:: sh

  python -m functionfs.tests.host
