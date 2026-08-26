import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    required property var sessionState

    readonly property bool hasError: sessionState.errors && sessionState.errors.length > 0
    readonly property bool hasProgress: sessionState.progress !== null && sessionState.progress !== undefined
    readonly property bool determinateProgress: hasProgress
        && sessionState.progress.total !== null
        && sessionState.progress.total !== undefined
    readonly property bool hasWarning: sessionState.ratings && sessionState.ratings.phase === "missing"
    readonly property bool shown: hasError || hasProgress || hasWarning
    readonly property var activeError: hasError ? sessionState.errors[0] : null
    readonly property string bannerTitle: {
        if (root.activeError)
            return root.activeError.recoverable ? "Recoverable error" : "Application error"
        if (root.hasWarning)
            return "Ratings unavailable"
        if (root.hasProgress)
            return root.sessionState.progress.message
        return ""
    }
    readonly property string bannerMessage: {
        if (root.activeError)
            return root.activeError.message
        if (root.hasWarning)
            return root.sessionState.ratings.message
        return "The current view remains available while work continues."
    }
    readonly property color bannerColor: {
        if (root.hasError)
            return Theme.errorDark
        if (root.hasWarning)
            return Theme.warningDark
        return Theme.surfaceHigh
    }
    readonly property color bannerBorderColor: {
        if (root.hasError)
            return Theme.error
        if (root.hasWarning)
            return Theme.warning
        return Theme.outline
    }

    visible: shown
    implicitHeight: shown ? content.implicitHeight + 20 : 0
    color: root.bannerColor
    border.color: root.bannerBorderColor
    border.width: 1
    radius: Theme.radius

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 10
        spacing: 12

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3

            Label {
                Layout.fillWidth: true
                text: root.bannerTitle
                color: Theme.text
                font.bold: true
            }

            Label {
                Layout.fillWidth: true
                text: root.bannerMessage
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            Label {
                Layout.fillWidth: true
                visible: root.hasError
                    && root.sessionState.ratings.phase === "failed"
                text: root.sessionState.ratings.message
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            ProgressBar {
                Layout.fillWidth: true
                visible: root.hasProgress
                indeterminate: root.hasProgress && !root.determinateProgress
                from: 0
                to: root.determinateProgress ? root.sessionState.progress.total : 1
                value: root.determinateProgress
                    && root.sessionState.progress.completed !== null
                    && root.sessionState.progress.completed !== undefined
                    ? root.sessionState.progress.completed : 0
                Accessible.name: root.hasProgress ? root.sessionState.progress.message : ""
            }
        }

        DimensionalButton {
            id: ratingsDownloadButton
            visible: root.hasWarning
                && root.sessionState.ratings.set_code !== null
                && root.sessionState.ratings.set_code !== undefined
            text: "Download ratings"
            Accessible.name: "Download ratings"
            objectName: "ratingsDownloadButton"
            onClicked: {
                ratingsDownloadDialog.returnFocusItem = ratingsDownloadButton
                ratingsDownloadDialog.open()
            }
        }

        DimensionalButton {
            objectName: "sessionErrorRetryButton"
            visible: root.activeError && root.activeError.recoverable
            text: "Retry"
            Accessible.name: "Retry failed operation"
            Accessible.description: "Retries the published recoverable error."
            onClicked: {
                const error = root.activeError
                if (error)
                    sessionProvider.retryError(error.error_id)
            }
        }

        DimensionalButton {
            objectName: "sessionErrorDismissButton"
            visible: root.activeError
            text: "Dismiss"
            accented: false
            Accessible.name: "Dismiss error"
            Accessible.description: "Dismisses the published error."
            onClicked: {
                const error = root.activeError
                if (error)
                    sessionProvider.dismissError(error.error_id)
            }
        }
    }

    Dialog {
        id: ratingsDownloadDialog
        objectName: "ratingsDownloadDialog"
        property var returnFocusItem: null
        implicitWidth: 400
        modal: true
        focus: true
        parent: Overlay.overlay
        title: "Download ratings?"
        onClosed: {
            if (returnFocusItem)
                returnFocusItem.forceActiveFocus()
        }

        Label {
            width: 360
            text: "Download 17Lands ratings for "
                + root.sessionState.ratings.set_code
                + "? Neutral-prior recommendations remain available while it loads."
            color: Theme.text
            wrapMode: Text.WordWrap
        }

        footer: DialogButtonBox {
            DimensionalButton {
                objectName: "ratingsDownloadCancelButton"
                text: "Not now"
                accented: false
                Accessible.name: "Cancel ratings download"
                onClicked: ratingsDownloadDialog.close()
            }

            DimensionalButton {
                objectName: "ratingsDownloadConfirmButton"
                text: "Download ratings"
                Accessible.name: "Confirm ratings download"
                onClicked: {
                    sessionProvider.requestRatings()
                    ratingsDownloadDialog.close()
                }
            }
        }
    }
}

