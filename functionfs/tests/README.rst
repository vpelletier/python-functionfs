Running tests
=============

On device
---------

1. in a root shell:

  .. code:: sh

    modprobe libcomposite
    cd /sys/kernel/config/usb_gadget/
    mkdir g1
    cd g1/
    echo 0x1d6b > idVendor
    echo 0x0104 > idProduct
    mkdir strings/0x409
    echo "1234" > strings/0x409/serialnumber
    echo "Foo Corp." > strings/0x409/manufacturer
    hostname > strings/0x409/product
    # In case is is already loaded, which would make mkdir functions/ffs.test fail
    rmmod g_ffs
    rmmod usb_f_fs
    mkdir functions/ffs.test
    mkdir configs/c.1
    mkdir configs/c.1/strings/0x409
    echo "test"> configs/c.1/strings/0x409/configuration
    ln -s functions/ffs.test configs/c.1
    mount -t functionfs -o uid=$SOME_USER test /mnt

2. in a shell running as $SOME_USER:

  .. code:: sh

    python -m functionfs.tests.device /mnt

3. in root shell again:

  .. code:: sh

    echo ... > /sys/kernel/config/usb_gadget/g1/UDC

Replacing "..." with the appropriate USB device controller, as listed in /sys/class/udc .

On host
-------

In a shell with permissions to open the device:

.. code:: sh

  python -m functionfs.tests.host
