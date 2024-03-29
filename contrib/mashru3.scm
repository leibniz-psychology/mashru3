(define-module (mashru3)
  #:use-module ((guix licenses) #:prefix license:)
  #:use-module (gnu packages)
  #:use-module (gnu packages compression)
  #:use-module (gnu packages backup)
  #:use-module (gnu packages base)
  #:use-module (gnu packages check)
  #:use-module (gnu packages python-xyz)
  #:use-module (gnu packages rsync)
  #:use-module (gnu packages acl)
  #:use-module (gnu packages nfs)
  #:use-module (gnu packages time)
  #:use-module (gnu packages kerberos)
  #:use-module (gnu packages package-management)
  #:use-module (guix packages)
  #:use-module (guix download)
  #:use-module (guix build-system python)
  #:use-module (guix gexp))

(define %source-dir (dirname (dirname (current-filename))))

;; Avoid including pre-build files like *.egg-info, so we can easily run it
;; from a development source tree.
(define (select? file stat)
  (let ((local-name (substring file (+ (string-length %source-dir) 1))))
    (not
      (or
        (string-suffix? ".egg-info" local-name)))))

(package
  (name "mashru3")
  (version "0.1")
  (source (local-file %source-dir #:recursive? #t #:select? select?))
  (build-system python-build-system)
  (arguments
   `(#:phases
     (modify-phases %standard-phases
       (add-after 'unpack 'patch-paths
         (lambda* (#:key inputs native-inputs #:allow-other-keys)
           (substitute* "mashru3/config.py"
             (("'rsync'") (string-append "'" (assoc-ref inputs "rsync") "/bin/rsync'"))
             ;(("'nfs4_setfacl'") (string-append "'" (assoc-ref inputs "nfs4-acl-tools") "/bin/nfs4_setfacl'"))
             ;(("'nfs4_getfacl'") (string-append "'" (assoc-ref inputs "nfs4-acl-tools") "/bin/nfs4_getfacl'"))
             (("'setfacl'") (string-append "'" (assoc-ref inputs "acl") "/bin/setfacl'"))
             (("'tar'") (string-append "'" (assoc-ref inputs "tar") "/bin/tar'"))
             (("'zip'") (string-append "'" (assoc-ref inputs "zip") "/bin/zip'"))
             (("'unzip'") (string-append "'" (assoc-ref inputs "unzip") "/bin/unzip'"))
             (("'lzip'") (string-append "'" (assoc-ref inputs "lzip") "/bin/lzip'"))
             (("'guix'") (string-append "'" (assoc-ref inputs "guix") "/bin/guix'"))
             (("'borg'") (string-append "'" (assoc-ref inputs "borg") "/bin/borg'")))
           (substitute* "mashru3/krb5.py"
             (("find_library \\('krb5'\\)")
              (string-append "'" (assoc-ref inputs "mit-krb5") "/lib/libkrb5.so'")))))
       (replace 'check
         (lambda* (#:key tests? #:allow-other-keys)
          (when tests?
            (invoke "pytest")))))))
  (inputs
   `(("python-unidecode" ,python-unidecode)
     ("python-pyyaml" ,python-pyyaml)
     ("python-magic" ,python-magic)
     ;; Need features from Python 3.9
     ("python-importlib-resources" ,python-importlib-resources)
     ("rsync" ,rsync) ; for rsync
     ("python-pylibacl" ,python-pylibacl)
     ("acl" ,acl) ; for setfacl
     ("nfs4-acl-tools" ,nfs4-acl-tools) ; for nfs4_setfacl
     ("python-pytz" ,python-pytz)
     ("zip" ,zip)
     ("unzip" ,unzip)
     ("tar" ,tar)
     ("lzip" ,lzip)
     ("guix" ,guix)
     ("mit-krb5" ,mit-krb5)
     ("borg" ,borg)))
  (native-inputs `(("python-pytest" ,python-pytest)))
  (home-page #f)
  (synopsis #f)
  (description #f)
  (license #f))

