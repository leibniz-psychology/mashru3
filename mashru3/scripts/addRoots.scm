;;;; Copyright 2021 Leibniz Institute for Psychology
;;;;
;;;; Permission is hereby granted, free of charge, to any person obtaining a copy
;;;; of this software and associated documentation files (the "Software"), to deal
;;;; in the Software without restriction, including without limitation the rights
;;;; to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
;;;; copies of the Software, and to permit persons to whom the Software is
;;;; furnished to do so, subject to the following conditions:
;;;;
;;;; The above copyright notice and this permission notice shall be included in
;;;; all copies or substantial portions of the Software.
;;;;
;;;; THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
;;;; IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
;;;; FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
;;;; AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
;;;; LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
;;;; OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
;;;; SOFTWARE.

;; Helper script that searches argv[1] for symlinks pointing to the store and
;; adds them as indirect roots, so the GC does not sweep them away. Prints
;; every symlink path to stdout.

(use-modules (guix store) (guix build utils))

(define (is-store-symlink path stat)
  (and
	(eq? (stat:type stat) 'symlink)
	(store-path? (readlink path))))

(let ((store (open-connection))
	  (base (canonicalize-path (car (cdr (program-arguments))))))
  (for-each
	(lambda (path)
	  (display (string-append path "\n"))
	  (add-indirect-root store path))
	(find-files base is-store-symlink)))

