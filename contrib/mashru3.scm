(define-module (mashru3)
  #:use-module ((guix licenses) #:prefix license:)
  #:use-module (gnu packages)
  #:use-module (gnu packages compression)
  #:use-module (gnu packages base)
  #:use-module (gnu packages python-xyz)
  #:use-module (gnu packages rsync)
  #:use-module (gnu packages acl)
  #:use-module (gnu packages nfs)
  #:use-module (gnu packages time)
  #:use-module (gnu packages kerberos)
  #:use-module (guix packages)
  #:use-module (guix download)
  #:use-module (guix build-system python)
  #:use-module (guix gexp))

(define %source-dir (dirname (dirname (current-filename))))

(package
  (name "mashru3")
  (version "0.1")
  (source (local-file %source-dir #:recursive? #t))
  (build-system python-build-system)
  (arguments
   `(#:tests? #f ; no tests
     #:phases
     (modify-phases %standard-phases
       (add-after 'unpack 'patch-paths
         (lambda* (#:key inputs native-inputs #:allow-other-keys)
           (substitute* "mashru3/cli.py"
             (("'rsync'") (string-append "'" (assoc-ref inputs "rsync") "/bin/rsync'"))
             (("'nfs4_setfacl'") (string-append "'" (assoc-ref inputs "nfs4-acl-tools") "/bin/nfs4_setfacl'"))
             (("'nfs4_getfacl'") (string-append "'" (assoc-ref inputs "nfs4-acl-tools") "/bin/nfs4_getfacl'"))
             (("'setfacl'") (string-append "'" (assoc-ref inputs "acl") "/bin/setfacl'"))
             (("'getfacl'") (string-append "'" (assoc-ref inputs "acl") "/bin/getfacl'"))
             (("(TAR_PROGRAM = )'tar'" all prefix) (string-append prefix "'" (assoc-ref inputs "tar") "/bin/tar'"))
             (("(ZIP_PROGRAM = )'zip'" all prefix) (string-append prefix "'" (assoc-ref inputs "zip") "/bin/zip'"))
             (("(UNZIP_PROGRAM = )'unzip'" all prefix) (string-append prefix "'" (assoc-ref inputs "unzip") "/bin/unzip'"))
             (("(LZIP_PROGRAM = )'lzip'" all prefix) (string-append prefix "'" (assoc-ref inputs "lzip") "/bin/lzip'")))
           (substitute* "mashru3/krb5.py"
             (("find_library \\('krb5'\\)")
              (string-append "'" (assoc-ref inputs "mit-krb5") "/lib/libkrb5.so'"))))))))
  (inputs
   `(("python-unidecode" ,python-unidecode)
     ("python-pyyaml" ,python-pyyaml)
     ("python-magic" ,python-magic)
     ("rsync" ,rsync) ; for rsync
     ("acl" ,acl) ; for setfacl
     ("nfs4-acl-tools" ,nfs4-acl-tools) ; for nfs4_setfacl
     ("python-pytz" ,python-pytz)
     ("zip" ,zip)
     ("unzip" ,unzip)
     ("tar" ,tar)
     ("lzip" ,lzip)
     ("mit-krb5" ,mit-krb5)))
  (home-page #f)
  (synopsis #f)
  (description #f)
  (license #f))

