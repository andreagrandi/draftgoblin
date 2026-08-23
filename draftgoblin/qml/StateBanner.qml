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

    visible: shown
    implicitHeight: shown ? content.implicitHeight + 20 : 0
    color: hasError ? Theme.errorDark : hasWarning ? Theme.warningDark : Theme.surfaceHigh
    border.color: hasError ? Theme.error : hasWarning ? Theme.warning : Theme.outline
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
                text: root.hasError
                    ? "Recoverable error"
                    : root.hasWarning
                        ? "Ratings unavailable"
                        : root.hasProgress ? root.sessionState.progress.message : ""
                color: Theme.text
                font.bold: true
            }

            Label {
                Layout.fillWidth: true
                text: root.hasError
                    ? root.activeError.message
                    : root.hasWarning
                        ? root.sessionState.ratings.message
                        : "The current view remains available while work continues."
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

        Button {
            visible: root.hasWarning
            text: "Download ratings"
            onClicked: mockProvider.requestRatings()
        }

        Button {
            visible: root.hasError && root.activeError.recoverable
            text: "Retry"
            onClicked: mockProvider.retryError(root.activeError.error_id)
        }

        Button {
            visible: root.hasError
            text: "Dismiss"
            onClicked: mockProvider.dismissError(root.activeError.error_id)
        }
    }
}

