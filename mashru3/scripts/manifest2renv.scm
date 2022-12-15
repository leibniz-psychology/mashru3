(use-modules (guix build json)
             (guix ui)
             (guix profiles)
             (guix packages)
             (guix build-system r)
             (guix git-download)
             (gnu packages statistics)
             (ice-9 match)
             (srfi srfi-1)
             (srfi srfi-26))

(define (package-upstream-name-for-r package)
  "Try to find an upstream name for PACKAGE, falling back to the package
name minus prefix."
  (or (assq-ref (package-properties package) 'upstream-name)
      ;; Try to get it from the package URI, assuming standardized
      ;; file names.
      (and=> (package->uri package) package-name-from-uri)
      ;; Simply drop r- prefix
      (string-drop (package-name package) 2)))

(define (package->uri package)
  "Get first URI from PACKAGE’s origin or #f."
  (match (origin-uri (package-source package))
    ((or (? string? uri) (uri _ ...))
      uri)
    (_ #f)))

(define (package-name-from-uri uri)
  (car (string-split (basename uri) #\_)))

(define (cran-uri? uri)
  "Does URI refer to CRAN or a mirror?"
  (or
    (string-prefix? "mirror://cran/" uri)
    (string-prefix? "https://cran.r-project.org/" uri)
    ;; MRAN is just a CRAN mirror.
    (string-prefix? "https://mran.microsoft.com/src/contrib/" uri)))

(define (bioconductor-uri? uri)
  "Does URI refer to Bioconductor?"
  (string-prefix? "https://bioconductor.org/packages/release/" uri))

(define (package->renv-repository package)
  "Create renv.lock repository fields for PACKAGE, depending on
PACKAGE-SOURCE."
  (match (origin-uri (package-source package))
    ((or (? string? uri) (uri _ ...))
      (match uri
        ((? cran-uri? uri)
          `((Source . "Repository")
            (Repository . "CRAN")))
        ((? bioconductor-uri? uri)
          `((Source . "Repository")
            (Repository . "Bioconductor")))
        (_ (begin
             (format (current-error-port) "Unsupported URI ~a for package ~a~%" uri package)
             '()))))
    ((? git-reference? origin) `((Source . "git")
                                 (RemoteType . "git")
                                 (RemoteUrl . ,(git-reference-url origin))
                                 (RemoteRef . ,(git-reference-commit origin))))
    ;; No support for Mercurial/hg in renv.
    (origin
      (begin
        (format (current-error-port) "Unsupported origin ~a for package ~a~%" origin package)
        '()))))

(define (manifest-entry->packages entry)
  "Retrieve package and its dependency packages from manifest entry."
  (cons (manifest-entry-item entry)
    (map manifest-entry-item (manifest-entry-dependencies entry))))

(define (main manifest-path)
  (let* ((manifest (load* manifest-path (make-user-module '((guix profiles) (gnu)))))
         (manifest-packages (append-map manifest-entry->packages (manifest-entries manifest)))
         (renv-packages
           (cons '@ ; Turn into JSON object.
             (fold
               (lambda (package tail)
                 (if (eq? (package-build-system package) r-build-system)
                   (let ((name (package-upstream-name-for-r package)))
                     (cons
                      `(,name .
                        (@ (Package . ,name)
                           (Version . ,(package-version package))
                           ,@(package->renv-repository package)))
                        tail))
                   tail))
               '()
               manifest-packages)))
         (renv-lock `(@
                       (R . (@
                              (Version . ,(package-version r))
                              (Repositories . ((@
                                               (Name . "CRAN")
                                               (URL . "http://cran.r-project.org/"))))))
                       (Packages . ,renv-packages))))
    (write-json renv-lock (current-output-port))))

(match (program-arguments)
  ((_ manifest-path) (main manifest-path))
  ((progname) (format (current-error-port) "Convert Guix manifest.scm into renv.lock~%Usage: guix repl -- ~a <manifest.scm> > renv.lock~%" progname)))
